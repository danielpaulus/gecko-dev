# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import hashlib
import json
import logging
import os
from datetime import datetime
from pprint import pformat
from urllib.parse import urlparse

from mozbuild.util import ReadOnlyDict, memoize
from mozversioncontrol import get_repository_object
from gecko_taskgraph.util.schema import validate_schema
from voluptuous import (
    ALLOW_EXTRA,
    Any,
    Required,
    Schema,
)

from . import GECKO
from .util.attributes import release_level

logger = logging.getLogger(__name__)


class ParameterMismatch(Exception):
    """Raised when a parameters.yml has extra or missing parameters."""


@memoize
def get_head_ref():
    return get_repository_object(GECKO).head_ref


def get_contents(path):
    with open(path, "r") as fh:
        contents = fh.readline().rstrip()
    return contents


def get_version(product_dir="browser"):
    version_path = os.path.join(GECKO, product_dir, "config", "version_display.txt")
    return get_contents(version_path)


def get_app_version(product_dir="browser"):
    app_version_path = os.path.join(GECKO, product_dir, "config", "version.txt")
    return get_contents(app_version_path)


base_schema = Schema(
    {
        Required("app_version"): str,
        Required("backstop"): bool,
        Required("base_repository"): str,
        Required("build_date"): int,
        Required("build_number"): int,
        Required("do_not_optimize"): [str],
        Required("existing_tasks"): {str: str},
        Required("filters"): [str],
        Required("head_ref"): str,
        Required("head_repository"): str,
        Required("head_rev"): str,
        Required("hg_branch"): str,
        Required("level"): str,
        Required("message"): str,
        Required("moz_build_date"): str,
        Required("next_version"): Any(None, str),
        Required("optimize_strategies"): Any(None, str),
        Required("optimize_target_tasks"): bool,
        Required("owner"): str,
        Required("phabricator_diff"): Any(None, str),
        Required("project"): str,
        Required("pushdate"): int,
        Required("pushlog_id"): str,
        Required("release_enable_emefree"): bool,
        Required("release_enable_partner_repack"): bool,
        Required("release_enable_partner_attribution"): bool,
        Required("release_eta"): Any(None, str),
        Required("release_history"): {str: dict},
        Required("release_partners"): Any(None, [str]),
        Required("release_partner_config"): Any(None, dict),
        Required("release_partner_build_number"): int,
        Required("release_type"): str,
        Required("release_product"): Any(None, str),
        Required("required_signoffs"): [str],
        Required("signoff_urls"): dict,
        # target-kind is not included, since it should never be
        # used at run-time
        Required("target_tasks_method"): str,
        Required("tasks_for"): str,
        Required("test_manifest_loader"): str,
        Required("try_mode"): Any(None, str),
        Required("try_options"): Any(None, dict),
        Required("try_task_config"): dict,
        Required("version"): str,
    }
)


def extend_parameters_schema(schema):
    """
    Extend the schema for parameters to include per-project configuration.

    This should be called by the `gecko_taskgraph.register` function in the
    graph-configuration.
    """
    global base_schema
    base_schema = base_schema.extend(schema)


class Parameters(ReadOnlyDict):
    """An immutable dictionary with nicer KeyError messages on failure"""

    def __init__(self, strict=True, **kwargs):
        self.strict = strict
        self.spec = kwargs.pop("spec", "defaults")
        self._id = None

        if not self.strict:
            # apply defaults to missing parameters
            kwargs = Parameters._fill_defaults(**kwargs)

        ReadOnlyDict.__init__(self, **kwargs)

    @property
    def id(self):
        if not self._id:
            self._id = hashlib.sha256(
                json.dumps(self, sort_keys=True).encode("utf-8")
            ).hexdigest()[:12]

        return self._id

    @staticmethod
    def format_spec(spec):
        """
        Get a friendly identifier from a parameters specifier.

        Args:
            spec (str): Parameters specifier.

        Returns:
            str: Name to identify parameters by.
        """
        if spec is None:
            return "defaults"

        if any(spec.startswith(s) for s in ("task-id=", "project=")):
            return spec

        result = urlparse(spec)
        if result.scheme in ("http", "https"):
            spec = result.path

        return os.path.splitext(os.path.basename(spec))[0]

    @staticmethod
    def _fill_defaults(**kwargs):
        now = datetime.utcnow()
        epoch = datetime.utcfromtimestamp(0)
        seconds_from_epoch = int((now - epoch).total_seconds())

        defaults = {
            "app_version": get_app_version(),
            "backstop": False,
            "base_repository": "https://hg.mozilla.org/mozilla-unified",
            "build_date": seconds_from_epoch,
            "build_number": 1,
            "do_not_optimize": [],
            "existing_tasks": {},
            "filters": ["target_tasks_method"],
            "head_ref": get_head_ref(),
            "head_repository": "https://hg.mozilla.org/mozilla-central",
            "head_rev": get_head_ref(),
            "hg_branch": "default",
            "level": "3",
            "message": "",
            "moz_build_date": now.strftime("%Y%m%d%H%M%S"),
            "next_version": None,
            "optimize_strategies": None,
            "optimize_target_tasks": True,
            "owner": "nobody@mozilla.com",
            "phabricator_diff": None,
            "project": "mozilla-central",
            "pushdate": seconds_from_epoch,
            "pushlog_id": "0",
            "release_enable_emefree": False,
            "release_enable_partner_repack": False,
            "release_enable_partner_attribution": False,
            "release_eta": "",
            "release_history": {},
            "release_partners": [],
            "release_partner_config": None,
            "release_partner_build_number": 1,
            "release_product": None,
            "release_type": "nightly",
            "required_signoffs": [],
            "signoff_urls": {},
            "target_tasks_method": "default",
            "tasks_for": "hg-push",
            "test_manifest_loader": "default",
            "try_mode": None,
            "try_options": None,
            "try_task_config": {},
            "version": get_version(),
        }

        for name, default in defaults.items():
            if name not in kwargs:
                kwargs[name] = default

        return kwargs

    def check(self):
        schema = (
            base_schema if self.strict else base_schema.extend({}, extra=ALLOW_EXTRA)
        )
        validate_schema(schema, self.copy(), "Invalid parameters:")

    def __getitem__(self, k):
        try:
            return super().__getitem__(k)
        except KeyError:
            raise KeyError(f"taskgraph parameter {k!r} not found")

    def is_try(self):
        """
        Determine whether this graph is being built on a try project or for
        `mach try fuzzy`.
        """
        return "try" in self["project"] or self["try_mode"] == "try_select"

    def file_url(self, path, pretty=False):
        """
        Determine the VCS URL for viewing a file in the tree, suitable for
        viewing by a human.

        :param text_type path: The path, relative to the root of the repository.
        :param bool pretty: Whether to return a link to a formatted version of the
            file, or the raw file version.
        :return text_type: The URL displaying the given path.
        """
        if path.startswith("comm/"):
            path = path[len("comm/") :]
            repo = self["comm_head_repository"]
            rev = self["comm_head_rev"]
        else:
            repo = self["head_repository"]
            rev = self["head_rev"]

        endpoint = "file" if pretty else "raw-file"
        return f"{repo}/{endpoint}/{rev}/{path}"

    def release_level(self):
        """
        Whether this is a staging release or not.

        :return str: One of "production" or "staging".
        """
        return release_level(self["project"])

    def __str__(self):
        return f"Parameters(id={self.id}) (from {self.format_spec(self.spec)})"

    def __repr__(self):
        return pformat(dict(self), indent=2)


def load_parameters_file(spec, strict=True, overrides=None, trust_domain=None):
    """
    Load parameters from a path, url, decision task-id or project.

    Examples:
        task-id=fdtgsD5DQUmAQZEaGMvQ4Q
        project=mozilla-central
    """
    import requests
    from gecko_taskgraph.util.taskcluster import get_artifact_url, find_task_id
    from gecko_taskgraph.util import yaml

    if overrides is None:
        overrides = {}

    if not spec:
        return Parameters(strict=strict, **overrides)

    try:
        # reading parameters from a local parameters.yml file
        f = open(spec)
    except OSError:
        # fetching parameters.yml using task task-id, project or supplied url
        task_id = None
        if spec.startswith("task-id="):
            task_id = spec.split("=")[1]
        elif spec.startswith("project="):
            if trust_domain is None:
                raise ValueError(
                    "Can't specify parameters by project "
                    "if trust domain isn't supplied.",
                )
            index = "{trust_domain}.v2.{project}.latest.taskgraph.decision".format(
                trust_domain=trust_domain,
                project=spec.split("=")[1],
            )
            task_id = find_task_id(index)

        if task_id:
            spec = get_artifact_url(task_id, "public/parameters.yml")
        logger.info(f"Loading parameters from {spec}")
        resp = requests.get(spec, stream=True)
        resp.raise_for_status()
        f = resp.raw

    if spec.endswith(".yml"):
        kwargs = yaml.load_stream(f)
    elif spec.endswith(".json"):
        kwargs = json.load(f)
    else:
        raise TypeError(f"Parameters file `{spec}` is not JSON or YAML")

    kwargs.update(overrides)

    return Parameters(strict=strict, **kwargs)


def parameters_loader(spec, strict=True, overrides=None):
    def get_parameters(graph_config):
        parameters = load_parameters_file(
            spec,
            strict=strict,
            overrides=overrides,
            trust_domain=graph_config["trust-domain"],
        )
        parameters.check()
        return parameters

    return get_parameters
