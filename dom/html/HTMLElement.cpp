/* -*- Mode: C++; tab-width: 8; indent-tabs-mode: nil; c-basic-offset: 2 -*- */
/* vim: set ts=8 sts=2 et sw=2 tw=80: */
/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "mozilla/dom/HTMLElement.h"

#include "mozilla/dom/CustomElementRegistry.h"
#include "mozilla/dom/HTMLElementBinding.h"
#include "nsContentUtils.h"

namespace mozilla::dom {

HTMLElement::HTMLElement(already_AddRefed<mozilla::dom::NodeInfo>&& aNodeInfo)
    : nsGenericHTMLFormElement(std::move(aNodeInfo)) {
  if (NodeInfo()->Equals(nsGkAtoms::bdi)) {
    AddStatesSilently(NS_EVENT_STATE_DIR_ATTR_LIKE_AUTO);
  }
}

NS_IMPL_CYCLE_COLLECTION_INHERITED(HTMLElement, nsGenericHTMLFormElement)

// QueryInterface implementation for HTMLElement

NS_INTERFACE_MAP_BEGIN_CYCLE_COLLECTION(HTMLElement)
  NS_INTERFACE_MAP_ENTRY_TEAROFF(nsIFormControl, GetElementInternals())
NS_INTERFACE_MAP_END_INHERITING(nsGenericHTMLFormElement)

NS_IMPL_ADDREF_INHERITED(HTMLElement, nsGenericHTMLFormElement)
NS_IMPL_RELEASE_INHERITED(HTMLElement, nsGenericHTMLFormElement)

NS_IMPL_ELEMENT_CLONE(HTMLElement)

JSObject* HTMLElement::WrapNode(JSContext* aCx,
                                JS::Handle<JSObject*> aGivenProto) {
  return dom::HTMLElement_Binding::Wrap(aCx, this, aGivenProto);
}

void HTMLElement::SetCustomElementDefinition(
    CustomElementDefinition* aDefinition) {
  // Always create an ElementInternal for form-associated custom element as the
  // Form related implementation lives in ElementInternal which implements
  // nsIFormControl. It is okay for the attachElementInternal API as there is a
  // separated flag for whether attachElementInternal is called.
  if (aDefinition && !aDefinition->IsCustomBuiltIn() &&
      aDefinition->mFormAssociated) {
    CustomElementData* data = GetCustomElementData();
    MOZ_ASSERT(data);
    data->GetOrCreateElementInternals(this);
  }
  nsGenericHTMLFormElement::SetCustomElementDefinition(aDefinition);
}

// https://html.spec.whatwg.org/commit-snapshots/53bc3803433e1c817918b83e8a84f3db900031dd/#dom-attachinternals
already_AddRefed<ElementInternals> HTMLElement::AttachInternals(
    ErrorResult& aRv) {
  CustomElementData* ceData = GetCustomElementData();

  // 1. If element's is value is not null, then throw a "NotSupportedError"
  //    DOMException.
  if (nsAtom* isAtom = ceData ? ceData->GetIs(this) : nullptr) {
    aRv.ThrowNotSupportedError(nsPrintfCString(
        "Cannot attach ElementInternals to a customized built-in element "
        "'%s'",
        NS_ConvertUTF16toUTF8(isAtom->GetUTF16String()).get()));
    return nullptr;
  }

  // 2. Let definition be the result of looking up a custom element definition
  //    given element's node document, its namespace, its local name, and null
  //    as is value.
  nsAtom* nameAtom = NodeInfo()->NameAtom();
  CustomElementDefinition* definition = nullptr;
  if (ceData) {
    definition = ceData->GetCustomElementDefinition();

    // If the definition is null, the element possible hasn't yet upgraded.
    // Fallback to use LookupCustomElementDefinition to find its definition.
    if (!definition) {
      definition = nsContentUtils::LookupCustomElementDefinition(
          NodeInfo()->GetDocument(), nameAtom, NodeInfo()->NamespaceID(),
          ceData->GetCustomElementType());
    }
  }

  // 3. If definition is null, then throw an "NotSupportedError" DOMException.
  if (!definition) {
    aRv.ThrowNotSupportedError(nsPrintfCString(
        "Cannot attach ElementInternals to a non-custom element '%s'",
        NS_ConvertUTF16toUTF8(nameAtom->GetUTF16String()).get()));
    return nullptr;
  }

  // 4. If definition's disable internals is true, then throw a
  //    "NotSupportedError" DOMException.
  if (definition->mDisableInternals) {
    aRv.ThrowNotSupportedError(nsPrintfCString(
        "AttachInternal() to '%s' is disabled by disabledFeatures",
        NS_ConvertUTF16toUTF8(nameAtom->GetUTF16String()).get()));
    return nullptr;
  }

  // If this is not a custom element, i.e. ceData is nullptr, we are unable to
  // find a definition and should return earlier above.
  MOZ_ASSERT(ceData);

  // 5. If element's attached internals is true, then throw an
  //    "NotSupportedError" DOMException.
  if (ceData->HasAttachedInternals()) {
    aRv.ThrowNotSupportedError(nsPrintfCString(
        "AttachInternals() has already been called from '%s'",
        NS_ConvertUTF16toUTF8(nameAtom->GetUTF16String()).get()));
    return nullptr;
  }

  // 6. If element's custom element state is not "precustomized" or "custom",
  //    then throw a "NotSupportedError" DOMException.
  if (ceData->mState != CustomElementData::State::ePrecustomized &&
      ceData->mState != CustomElementData::State::eCustom) {
    aRv.ThrowNotSupportedError(
        R"(Custom element state is not "precustomized" or "custom".)");
    return nullptr;
  }

  // 7. Set element's attached internals to true.
  ceData->AttachedInternals();

  // 8. Create a new ElementInternals instance targeting element, and return it.
  return do_AddRef(ceData->GetOrCreateElementInternals(this));
}

void HTMLElement::UpdateFormOwner() {
  MOZ_ASSERT(IsFormAssociatedElement());

  // If @form is set, the element *has* to be in a composed document,
  // otherwise it wouldn't be possible to find an element with the
  // corresponding id. If @form isn't set, the element *has* to have a parent,
  // otherwise it wouldn't be possible to find a form ancestor. We should not
  // call UpdateFormOwner if none of these conditions are fulfilled.
  if (HasAttr(kNameSpaceID_None, nsGkAtoms::form) ? IsInComposedDoc()
                                                  : !!GetParent()) {
    nsGenericHTMLFormElement::UpdateFormOwner(true, nullptr);
  }
  UpdateFieldSet(true);
}

void HTMLElement::SetFormInternal(HTMLFormElement* aForm, bool aBindToTree) {
  ElementInternals* internals = GetElementInternals();
  MOZ_ASSERT(internals);
  internals->SetForm(aForm);
}

HTMLFormElement* HTMLElement::GetFormInternal() const {
  ElementInternals* internals = GetElementInternals();
  MOZ_ASSERT(internals);
  return internals->GetForm();
}

void HTMLElement::SetFieldSetInternal(HTMLFieldSetElement* aFieldset) {
  ElementInternals* internals = GetElementInternals();
  MOZ_ASSERT(internals);
  internals->SetFieldSet(aFieldset);
}

HTMLFieldSetElement* HTMLElement::GetFieldSetInternal() const {
  ElementInternals* internals = GetElementInternals();
  MOZ_ASSERT(internals);
  return internals->GetFieldSet();
}

bool HTMLElement::CanBeDisabled() const { return IsFormAssociatedElement(); }

bool HTMLElement::DoesReadOnlyApply() const {
  return IsFormAssociatedElement();
}

bool HTMLElement::IsFormAssociatedElement() const {
  CustomElementData* data = GetCustomElementData();
  bool isFormAssociatedCustomElement = data && data->IsFormAssociated();
  MOZ_ASSERT_IF(
      isFormAssociatedCustomElement,
      StaticPrefs::dom_webcomponents_formAssociatedCustomElement_enabled());
  return isFormAssociatedCustomElement;
}

ElementInternals* HTMLElement::GetElementInternals() const {
  CustomElementData* data = GetCustomElementData();
  if (!data || !data->IsFormAssociated()) {
    // If the element is not a form associated custom element, it should not be
    // able to be QueryInterfaced to nsIFormControl and could not perform
    // the form operation, either, so we return nullptr here.
    return nullptr;
  }

  return data->GetElementInternals();
}

}  // namespace mozilla::dom

// Here, we expand 'NS_IMPL_NS_NEW_HTML_ELEMENT()' by hand.
// (Calling the macro directly (with no args) produces compiler warnings.)
nsGenericHTMLElement* NS_NewHTMLElement(
    already_AddRefed<mozilla::dom::NodeInfo>&& aNodeInfo,
    mozilla::dom::FromParser aFromParser) {
  RefPtr<mozilla::dom::NodeInfo> nodeInfo(aNodeInfo);
  auto* nim = nodeInfo->NodeInfoManager();
  return new (nim) mozilla::dom::HTMLElement(nodeInfo.forget());
}

// Distinct from the above in order to have function pointer that compared
// unequal to a function pointer to the above.
nsGenericHTMLElement* NS_NewCustomElement(
    already_AddRefed<mozilla::dom::NodeInfo>&& aNodeInfo,
    mozilla::dom::FromParser aFromParser) {
  RefPtr<mozilla::dom::NodeInfo> nodeInfo(aNodeInfo);
  auto* nim = nodeInfo->NodeInfoManager();
  return new (nim) mozilla::dom::HTMLElement(nodeInfo.forget());
}
