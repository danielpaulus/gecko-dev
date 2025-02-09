/* -*- Mode: IDL; tab-width: 2; indent-tabs-mode: nil; c-basic-offset: 2 -*- */
/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/.
 *
 * The origin of this IDL file is
 * https://html.spec.whatwg.org/#elementinternals
 */

[Pref="dom.webcomponents.elementInternals.enabled", Exposed=Window]
interface ElementInternals {
  // Shadow root access
  readonly attribute ShadowRoot? shadowRoot;

  // Form-associated custom elements
  [Pref="dom.webcomponents.formAssociatedCustomElement.enabled", Throws]
  readonly attribute HTMLFormElement? form;

  [Pref="dom.webcomponents.formAssociatedCustomElement.enabled", Throws]
  readonly attribute NodeList labels;
};
