# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.


import os
import datetime
import functools
import requests
import logging
import taskcluster_urls as liburls
from mozbuild.util import memoize
from requests.packages.urllib3.util.retry import Retry
from taskcluster import Hooks
from gecko_taskgraph.task import Task
from gecko_taskgraph.util import yaml

logger = logging.getLogger(__name__)

# this is set to true for `mach taskgraph action-callback --test`
testing = False

# Default rootUrl to use if none is given in the environment; this should point
# to the production Taskcluster deployment used for CI.
PRODUCTION_TASKCLUSTER_ROOT_URL = "https://firefox-ci-tc.services.mozilla.com"

# the maximum number of parallel Taskcluster API calls to make
CONCURRENCY = 50


@memoize
def get_root_url(use_proxy):
    """Get the current TASKCLUSTER_ROOT_URL.  When running in a task, this must
    come from $TASKCLUSTER_ROOT_URL; when run on the command line, we apply a
    defualt that points to the production deployment of Taskcluster.  If use_proxy
    is set, this attempts to get TASKCLUSTER_PROXY_URL instead, failing if it
    is not set."""
    if use_proxy:
        try:
            return os.environ["TASKCLUSTER_PROXY_URL"]
        except KeyError:
            if "TASK_ID" not in os.environ:
                raise RuntimeError(
                    "taskcluster-proxy is not available when not executing in a task"
                )
            else:
                raise RuntimeError("taskcluster-proxy is not enabled for this task")

    if "TASKCLUSTER_ROOT_URL" not in os.environ:
        if "TASK_ID" in os.environ:
            raise RuntimeError(
                "$TASKCLUSTER_ROOT_URL must be set when running in a task"
            )
        else:
            logger.debug("Using default TASKCLUSTER_ROOT_URL (Firefox CI production)")
            return PRODUCTION_TASKCLUSTER_ROOT_URL
    logger.debug(
        "Running in Taskcluster instance {}{}".format(
            os.environ["TASKCLUSTER_ROOT_URL"],
            " with taskcluster-proxy" if "TASKCLUSTER_PROXY_URL" in os.environ else "",
        )
    )
    return os.environ["TASKCLUSTER_ROOT_URL"]


def requests_retry_session(
    retries,
    backoff_factor=0.1,
    status_forcelist=(500, 502, 504),
    concurrency=CONCURRENCY,
    session=None,
):
    session = session or requests.Session()
    retry = Retry(
        total=retries,
        read=retries,
        connect=retries,
        backoff_factor=backoff_factor,
        status_forcelist=status_forcelist,
    )

    # Default HTTPAdapter uses 10 connections. Mount custom adapter to increase
    # that limit. Connections are established as needed, so using a large value
    # should not negatively impact performance.
    http_adapter = requests.adapters.HTTPAdapter(
        pool_connections=concurrency,
        pool_maxsize=concurrency,
        max_retries=retry,
    )
    session.mount("http://", http_adapter)
    session.mount("https://", http_adapter)

    return session


@memoize
def get_session():
    return requests_retry_session(retries=5)


def _do_request(url, method=None, **kwargs):
    if method is None:
        method = "post" if kwargs else "get"

    session = get_session()
    if method == "get":
        kwargs["stream"] = True
    response = getattr(session, method)(url, **kwargs)

    if response.status_code >= 400:
        # Consume content before raise_for_status, so that the connection can be
        # reused.
        response.content
    response.raise_for_status()
    return response


def _handle_artifact(path, response):
    if path.endswith(".json"):
        return response.json()
    if path.endswith(".yml"):
        return yaml.load_stream(response.text)
    response.raw.read = functools.partial(response.raw.read, decode_content=True)
    return response.raw


def get_artifact_url(task_id, path, use_proxy=False):
    artifact_tmpl = liburls.api(
        get_root_url(False), "queue", "v1", "task/{}/artifacts/{}"
    )
    data = artifact_tmpl.format(task_id, path)
    if use_proxy:
        # Until Bug 1405889 is deployed, we can't download directly
        # from the taskcluster-proxy.  Work around by using the /bewit
        # endpoint instead.
        # The bewit URL is the body of a 303 redirect, which we don't
        # want to follow (which fetches a potentially large resource).
        response = _do_request(
            os.environ["TASKCLUSTER_PROXY_URL"] + "/bewit",
            data=data,
            allow_redirects=False,
        )
        return response.text
    return data


def get_artifact(task_id, path, use_proxy=False):
    """
    Returns the artifact with the given path for the given task id.

    If the path ends with ".json" or ".yml", the content is deserialized as,
    respectively, json or yaml, and the corresponding python data (usually
    dict) is returned.
    For other types of content, a file-like object is returned.
    """
    response = _do_request(get_artifact_url(task_id, path, use_proxy))
    return _handle_artifact(path, response)


def list_artifacts(task_id, use_proxy=False):
    response = _do_request(get_artifact_url(task_id, "", use_proxy).rstrip("/"))
    return response.json()["artifacts"]


def get_artifact_prefix(task):
    prefix = None
    if isinstance(task, dict):
        prefix = task.get("attributes", {}).get("artifact_prefix")
    elif isinstance(task, Task):
        prefix = task.attributes.get("artifact_prefix")
    else:
        raise Exception(f"Can't find artifact-prefix of non-task: {task}")
    return prefix or "public/build"


def get_artifact_path(task, path):
    return f"{get_artifact_prefix(task)}/{path}"


def get_index_url(index_path, use_proxy=False, multiple=False):
    index_tmpl = liburls.api(get_root_url(use_proxy), "index", "v1", "task{}/{}")
    return index_tmpl.format("s" if multiple else "", index_path)


def find_task_id(index_path):
    try:
        response = _do_request(get_index_url(index_path))
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            raise KeyError(f"index path {index_path} not found")
        raise
    return response.json()["taskId"]


def get_artifact_from_index(index_path, artifact_path, use_proxy=False):
    full_path = index_path + "/artifacts/" + artifact_path
    response = _do_request(get_index_url(full_path, use_proxy))
    return _handle_artifact(full_path, response)


def list_tasks(index_path, use_proxy=False):
    """
    Returns a list of task_ids where each task_id is indexed under a path
    in the index. Results are sorted by expiration date from oldest to newest.
    """
    results = []
    data = {}
    while True:
        response = _do_request(
            get_index_url(index_path, use_proxy, multiple=True), json=data
        )
        response = response.json()
        results += response["tasks"]
        if response.get("continuationToken"):
            data = {"continuationToken": response.get("continuationToken")}
        else:
            break

    # We can sort on expires because in the general case
    # all of these tasks should be created with the same expires time so they end up in
    # order from earliest to latest action. If more correctness is needed, consider
    # fetching each task and sorting on the created date.
    results.sort(key=lambda t: parse_time(t["expires"]))
    return [t["taskId"] for t in results]


def insert_index(index_path, task_id, data=None, use_proxy=False):
    index_url = get_index_url(index_path, use_proxy=use_proxy)

    # Find task expiry.
    expires = get_task_definition(task_id, use_proxy=use_proxy)["expires"]

    response = _do_request(
        index_url,
        method="put",
        json={
            "taskId": task_id,
            "rank": 0,
            "data": data or {},
            "expires": expires,
        },
    )
    return response


def parse_time(timestamp):
    """Turn a "JSON timestamp" as used in TC APIs into a datetime"""
    return datetime.datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%S.%fZ")


def get_task_url(task_id, use_proxy=False):
    task_tmpl = liburls.api(get_root_url(use_proxy), "queue", "v1", "task/{}")
    return task_tmpl.format(task_id)


def get_task_definition(task_id, use_proxy=False):
    response = _do_request(get_task_url(task_id, use_proxy))
    return response.json()


def cancel_task(task_id, use_proxy=False):
    """Cancels a task given a task_id. In testing mode, just logs that it would
    have cancelled."""
    if testing:
        logger.info(f"Would have cancelled {task_id}.")
    else:
        _do_request(get_task_url(task_id, use_proxy) + "/cancel", json={})


def status_task(task_id, use_proxy=False):
    """Gets the status of a task given a task_id.

    In testing mode, just logs that it would have retrieved status.

    Args:
        task_id (str): A task id.
        use_proxy (bool): Whether to use taskcluster-proxy (default: False)

    Returns:
        dict: A dictionary object as defined here:
          https://docs.taskcluster.net/docs/reference/platform/queue/api#status
    """
    if testing:
        logger.info(f"Would have gotten status for {task_id}.")
    else:
        resp = _do_request(get_task_url(task_id, use_proxy) + "/status")
        status = resp.json().get("status", {})
        return status


def state_task(task_id, use_proxy=False):
    """Gets the state of a task given a task_id.

    In testing mode, just logs that it would have retrieved state. This is a subset of the
    data returned by :func:`status_task`.

    Args:
        task_id (str): A task id.
        use_proxy (bool): Whether to use taskcluster-proxy (default: False)

    Returns:
        str: The state of the task, one of
          ``pending, running, completed, failed, exception, unknown``.
    """
    if testing:
        logger.info(f"Would have gotten state for {task_id}.")
    else:
        status = status_task(task_id, use_proxy=use_proxy).get("state") or "unknown"
        return status


def rerun_task(task_id):
    """Reruns a task given a task_id. In testing mode, just logs that it would
    have reran."""
    if testing:
        logger.info(f"Would have rerun {task_id}.")
    else:
        _do_request(get_task_url(task_id, use_proxy=True) + "/rerun", json={})


def trigger_hook(hook_group_id, hook_id, hook_payload):
    hooks = Hooks({"rootUrl": get_root_url(True)})
    response = hooks.triggerHook(hook_group_id, hook_id, hook_payload)

    logger.info(
        "Task seen here: {}/tasks/{}".format(
            get_root_url(os.environ.get("TASKCLUSTER_PROXY_URL")),
            response["status"]["taskId"],
        )
    )


def get_current_scopes():
    """Get the current scopes.  This only makes sense in a task with the Taskcluster
    proxy enabled, where it returns the actual scopes accorded to the task."""
    auth_url = liburls.api(get_root_url(True), "auth", "v1", "scopes/current")
    resp = _do_request(auth_url)
    return resp.json().get("scopes", [])


def get_purge_cache_url(provisioner_id, worker_type, use_proxy=False):
    url_tmpl = liburls.api(
        get_root_url(use_proxy), "purge-cache", "v1", "purge-cache/{}/{}"
    )
    return url_tmpl.format(provisioner_id, worker_type)


def purge_cache(provisioner_id, worker_type, cache_name, use_proxy=False):
    """Requests a cache purge from the purge-caches service."""
    if testing:
        logger.info(
            "Would have purged {}/{}/{}.".format(
                provisioner_id, worker_type, cache_name
            )
        )
    else:
        logger.info(f"Purging {provisioner_id}/{worker_type}/{cache_name}.")
        purge_cache_url = get_purge_cache_url(provisioner_id, worker_type, use_proxy)
        _do_request(purge_cache_url, json={"cacheName": cache_name})


def send_email(address, subject, content, link, use_proxy=False):
    """Sends an email using the notify service"""
    logger.info(f"Sending email to {address}.")
    url = liburls.api(get_root_url(use_proxy), "notify", "v1", "email")
    _do_request(
        url,
        json={
            "address": address,
            "subject": subject,
            "content": content,
            "link": link,
        },
    )


def list_task_group_tasks(task_group_id):
    """Generate the tasks in a task group"""
    params = {}
    while True:
        url = liburls.api(
            get_root_url(False),
            "queue",
            "v1",
            f"task-group/{task_group_id}/list",
        )
        resp = _do_request(url, method="get", params=params).json()
        yield from resp["tasks"]
        if resp.get("continuationToken"):
            params = {"continuationToken": resp.get("continuationToken")}
        else:
            break


def list_task_group_incomplete_task_ids(task_group_id):
    states = ("running", "pending", "unscheduled")
    for task in [t["status"] for t in list_task_group_tasks(task_group_id)]:
        if task["state"] in states:
            yield task["taskId"]
