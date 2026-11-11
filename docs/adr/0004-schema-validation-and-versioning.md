# 0004: Schema validation at the boundary, and versioning rules

* Status: accepted
* Date: 2026-11-04

## Context and problem statement

Inbound XML must be checked before it can affect the ERP. The XSD should be enforced, but the Python standard library cannot validate against an XSD. The schema will also change over time. How do we validate, and how do we version?

## Decision drivers

* Standard library only for the reference implementation
* Clear, path-specific error messages for senders
* Safe evolution of the message format
* Defence against hostile XML

## Considered options

For validation:

1. Implement a small XSD-subset validator in the standard library, and cross-check it against lxml in tests
2. Require lxml (a third-party dependency, wrapping libxml2)
3. Hand-written Python checks with no XSD

For versioning:

* A. Major version in the XML namespace, minor version as an attribute, and additive-only changes within a major version
* B. Version in the file name or URL only
* C. No explicit version

## Decision outcome

Chosen: validation option **1**; versioning option **A**.

* `xsd.py` supports the subset the schema uses, and fails loudly (`SchemaError`) on features it does not support
* `tests/test_validation.py` compares results with lxml on valid and invalid documents when lxml is installed, which also proves that the XSD itself is well formed
* A change that breaks existing senders (removing or renaming an element, tightening a facet, changing meaning) creates a new namespace (`...:shipment-message:2`) and a new schema file. Both can be supported side by side during migration.
* A compatible change (a new optional element, a widened enumeration) bumps the `version` attribute's allowed values.
* A message in an unknown namespace or version is rejected with `VALIDATION_FAILED`, never guessed at.
* DOCTYPE and ENTITY declarations are refused before parsing.

### Consequences

* Good: no dependency, and the cross-check gives evidence the subset validator is faithful for the cases tested.
* Good: errors name the path and the reason.
* Bad: a custom validator is code to maintain, and it is not a complete XSD implementation. Real, complex schemas should use a complete engine, either lxml or a validator in another language.
* Bad: strict validation rejects messages with harmless extras, so sender and receiver must agree on how changes are announced.

## More information

[Patterns catalogue: schema validation](../patterns-catalogue.md#schema-validation). The schema is `src/mini_middleware/schemas/shipment_message.xsd`, and it is illustrative only.
