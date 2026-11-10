# Phase 4 — Deterministic Test Generation Engine Proposal

**Status:** Original architecture proposal, kept as design rationale. The Deterministic Test Generation Engine is **partially implemented** (Steps 1–6.3): `generate_test_suite()` and its output models are in `backend/app/services/test_generation/`, covered by `tests/test_test_generation.py`. The current implementation status is maintained in `AGENTS.md` §12 and `docs/ROADMAP.md`.

**Status labels used in this document:** *Implemented* (verified in code and tests), *Partial* (implemented for a narrower subset than proposed), *Planned* (not implemented, still intended), *Unsupported* (not implemented; reported with a diagnostic or the endpoint is skipped). Text without a label is the original proposal.

## 1. Purpose and responsibilities

The Deterministic Test Generation Engine converts the existing normalized API representation into repeatable, declarative test cases. It should help find contract violations by deriving request inputs and response expectations from the OpenAPI model without AI, random behavior, or network access.

The engine owns:

- selecting operations and supported request media types from `NormalizationResult.api`;
- resolving supported local reference edges lazily against the existing component registries;
- producing deterministic valid and intentionally invalid input values for an explicitly supported schema subset;
- representing response expectations and unsupported-generation diagnostics;
- assigning stable ordering and identifiers to generated cases.

The engine does not parse JSON/YAML, revalidate the OpenAPI document, issue HTTP requests, choose credentials, schedule runs, inspect a live API, or claim that unsupported schema semantics have been tested. Execution and result collection remain a later, separate service. Server URLs and security scheme definitions are descriptive inputs, not permission to access a target or credentials.

## 2. Repository findings: current pipeline and actual state

The implemented pipeline is:

```text
JSON/YAML text
    -> parse_openapi_document(content, filename)
    -> validated source dictionary
    -> normalize_openapi_document(document)
    -> NormalizationResult(ApiModel, diagnostics)
    -> generate_test_suite(result)   [implemented, partial]
    -> declarative generated test cases
    -> future, separate execution engine
```

### Implemented capabilities

- The Parser accepts JSON and YAML, applies a 5 MiB input limit, rejects unsupported extensions and YAML aliases, uses a safe YAML loader, checks OpenAPI version syntax/support, rejects invalid/external `$ref` strings, and invokes `openapi-spec-validator` for specification validation. The supported version families are OpenAPI 3.0.x and 3.1.x.
- The API Model is implemented with strict, frozen Pydantic v2 models. `Schema.raw` preserves JSON-compatible schema objects, including unknown keywords, boolean schemas, compositions and references; construction detaches the input via deep copy. The model does not turn each schema keyword into a typed field.
- The Normalizer is implemented. It extracts API metadata, root servers and security, component registries, and endpoints. It retains references as edges, merges path and operation parameters by `(name, in)`, records override diagnostics, and preserves operation security and server scopes. It rejects unsupported Path Item `$ref` usage rather than resolving it.
- The Normalizer sorts dictionary-backed registries and endpoint paths/methods deterministically while preserving meaningful array order such as security alternatives, tags, and OAuth scopes.
- Existing tests cover parser behavior, API Model invariants and serialization, normalization of metadata/components/endpoints/parameters/security/servers, health endpoints, and the Generator (`tests/test_test_generation.py`: output models, positive baselines, negative cases, local reference resolution, generation budgets and diagnostics).
- The Generator is *partially implemented*: `generate_test_suite()` in `backend/app/services/test_generation/generator.py`, with output models in `models.py`. The status of each proposed feature is marked in the sections below.

### What the model already exposes to a generator

| Input area | Current representation | Generator implication |
|---|---|---|
| Operation | `Endpoint(path, method, operation_id, summary, description, tags, deprecated, ...)` | Method and exact path template are ready for case attribution. Endpoint order is deterministic. |
| Parameters | Ordered `Endpoint.parameters: tuple[ParameterEntry, ...]`; concrete parameters retain `name`, `location`, `required`, `schema` or `content`, `style`, `explode`, `allow_reserved`, examples and `source_scope`. | The generator has effective path/operation declarations. `ParameterReference` values must be looked up in `Components.parameters` before their schema or requiredness is known. |
| Request bodies | `Endpoint.request_body` is absent (`None`), a typed `RequestBody` with `required` and media-type map, or a `LocalReference`. | Omission, optional body and required body are distinguishable. A body reference needs lazy lookup. |
| Media types | `MediaType` preserves `schema`, `example`, named `examples`, `encoding`, and extensions. | Multiple declared types are available. Choosing an encoding and serializing it into HTTP bytes is not part of generation’s first stage. |
| Responses | `Endpoint.responses` maps string keys (`200`, `2XX`, `default`) to `Response` or `LocalReference`; response content maps media types to `MediaType`. | Status keys and response contracts can be retained as expectations; referenced responses need lookup. No observed response exists until execution. |
| Reusable schemas | `Components.schemas: dict[str, Schema]`; each schema holds raw JSON-compatible data. | The generator must interpret supported schema keywords from `Schema.raw` and follow references lazily. Unknown keywords are preserved but not thereby supported for generation. |
| Other components | Typed parameter/request-body/response/security-scheme registries, plus opaque JSON-compatible `Components.other`. | Only typed registries are generation inputs in the MVP. Opaque categories must not be mistaken for fully modeled semantics. |
| Authentication | `ApiModel.security` and `Endpoint.security` are `tuple[SecurityRequirement, ...] | None`; security schemes are typed definitions or references. | `None`, empty tuple and anonymous requirement are distinct. Requirements are alternatives; schemes inside one requirement are cumulative. Credentials are absent by design. |
| Servers | Root `ApiModel.servers`; path and operation declarations on Endpoint are separate optional tuples. | Scope and absent-vs-empty states are retained. Effective base URL selection belongs to target configuration/execution, not to generated test data. |
| Schema distinctions | Raw content retains `nullable`, null types, `required`, `readOnly`, `writeOnly`, enum/const, primitive constraints, and compositions without normalization. | Semantics are available as source information. There is no typed schema API; the Generator interprets a small keyword subset with private helpers in `generator.py` (`_check_schema`, `_matches`, `_select_value`; *Partial*, see §6). Dialect must be taken from `ApiModel.openapi_version`. |

### Documentation discrepancy

`docs/API_MODEL_CONTRACT.md` and the current implementation status are synchronized: the Normalizer is implemented and covered by endpoint-normalization tests. `docs/API_MODEL_NORMALIZER_PROPOSAL.md` is an earlier, pre-implementation design proposal; retain it as historical context, not as a description of current implementation. This proposal uses the source and tests as current evidence.

## 3. Proposed architecture and processing pipeline

Use a small pure service that consumes the already normalized model. No API route or execution dependency is needed.

```text
NormalizationResult
  ├─ validate generator preconditions and select endpoints
  ├─ build read-only indexes over typed component registries
  ├─ generate deterministic request candidates for supported operations
  ├─ attach effective security metadata and response expectations
  ├─ record unsupported constructs rather than silently approximating them
  └─ return ordered GeneratedTestSuite
```

Each stage should be deterministic and local. A generation policy/configuration may later select categories or limits, but the Basic Edition MVP should use explicit code defaults rather than random seeds or an extensible plugin system. The output should not contain a resolved target URL or secret.

### Reference strategy

Use the existing hybrid representation: component registries remain canonical, and endpoint/schema references remain edges. The engine builds lookup indexes once per generation call and follows a reference only when a case needs that data. Do not inline the entire schema graph into endpoints.

- Resolve only valid local references already accepted by `LocalReference` and supported by a documented pointer subset.
- For parameter aliases, follow `ParameterReference` entries through `Components.parameters` until a concrete `Parameter` is found. Keep the original endpoint entry and its scope as case provenance.
- For request body and response references, follow their typed component registries to a concrete model as needed.
- For schema references inside raw schema data, look up `Components.schemas`; retain `$ref` siblings according to the document dialect/context rather than replacing the schema object blindly.
- Track visited canonical reference URIs. A repeated URI is a cycle; report a generation diagnostic for that case and do not recurse indefinitely. Apply explicit maximum traversal depth and generated-value count limits.
- Do not fetch, resolve, or accept external references. Do not imply that the Parser validator never resolves local references internally; the generator’s policy applies after normalization.
- The Parser’s real-validator test documents that a valid-looking OpenAPI 3.1 `$anchor` reference is currently unsupported by the installed validator. The initial generator should support JSON Pointer component references and report anchors/other unsupported reference forms unless a separate end-to-end test establishes support.

**Implementation status (reference strategy):** *Implemented, partial.* The Generator follows references lazily, but it does not use a separate resolver or a prebuilt index. Private `_resolve_parameter`, `_resolve_request_body`, `_resolve_response` and `_follow_schema_reference` in `generator.py` look targets up directly in the component registries (schemas through a per-call context variable). Supported forms are only `#/components/parameters/<name>`, `#/components/requestBodies/<name>`, `#/components/responses/<name>`, `#/components/schemas/<name>` (schema references on the request side only, see below) and `#/components/securitySchemes/<name>` (scheme aliases reached from security requirements), with percent-decoding, `~0`/`~1` decoding and alias chains. Missing targets, cycles and other forms (external references, `$defs`, anchors) produce a diagnostic with the source pointer and reference URI, and no case is built from them. Schema nesting is limited by `schema_depth_limit` (32 levels). Only `description`, `title`, `summary` and `x-` keywords may sit next to a schema `$ref`; any other sibling gives `unsupported_schema_reference_siblings`. Each resolved reference hop is charged to the generation budgets (§5). Schema reference resolution is used for request-side generation only: parameter schemas, request body schemas and the properties reached from them (including the nested numeric, string-length and array-size cases). Response schemas are preserved as declared in `ResponseExpectation`; `$ref`s inside them are not resolved, recursively expanded or evaluated (*Planned*). Top-level response references (`#/components/responses/<name>`) are resolved.

This approach preserves recursion and source fidelity while allowing the generator to obtain effective parameter or body schemas locally. Full expansion would duplicate large graphs, complicate cycles, and risk changing JSON Schema composition and `$ref` sibling semantics.

## 4. Proposed input and output contracts

### Input

```python
def generate_test_suite(
    result: NormalizationResult,
) -> GeneratedTestSuite:
    """Generate deterministic, declarative test cases from a normalized API."""
```

`NormalizationResult` is the single input contract. The engine consumes `result.api`; existing `result.diagnostics` may be copied to a generation report or ignored by case generation, but must not be conflated with generation diagnostics. The engine must not re-parse or mutate the source document.

### Output

*Implemented* in `backend/app/services/test_generation/models.py`: a generator-owned output contract rather than added fields on `ApiModel`. The code block below is the original proposal; the differences from the implementation are listed after it.

```python
from typing import Literal

from app.services.openapi.models import JsonValue, Schema, SecurityRequirement


class GeneratedTestSuite:
    api_title: str
    api_version: str
    openapi_version: str
    cases: tuple[GeneratedTestCase, ...]
    diagnostics: tuple[GenerationDiagnostic, ...]

class GeneratedTestCase:
    case_id: str                 # stable from category + endpoint + variant
    category: str                # implemented values: "positive" or "negative"
    path: str
    method: str
    parameters: tuple[GeneratedParameterValue, ...]
    request_body: GeneratedBody | None
    security: tuple[SecurityRequirement, ...] | None
    response_expectations: tuple[ResponseExpectation, ...]
    provenance: tuple[str, ...]  # stable OpenAPI pointers/references for review
    # Implemented but not in the original proposal:
    expected_outcome: str        # "response_contract_unasserted" or "request_rejection_status_unspecified"
    violated_constraint: str | None
    affected_property: str | None  # JSON Pointer relative to the request body

class GeneratedParameterValue:
    name: str
    location: Literal["path", "query", "header", "cookie"]
    value: JsonValue
    provenance: tuple[str, ...]  # source JSON Pointer, then any reference URIs followed

class GeneratedBody:
    media_type: str
    value: JsonValue

class ResponseExpectation:
    status_key: str
    media_types: tuple[str, ...]
    schemas_by_media_type: dict[str, Schema | None]
    response_reference_uri: str | None

class GenerationDiagnostic:
    code: str                    # stable machine-readable code
    message: str
    endpoint_path: str | None
    endpoint_method: str | None
    source_pointer: str | None
    reference_uri: str | None
```

The types above are implemented as strict, frozen Pydantic v2 models with `extra="forbid"` (`GeneratedTestSuite`, `GeneratedTestCase`, `GeneratedParameterValue`, `GeneratedBody`, `ResponseExpectation`, `GenerationDiagnostic`). They are exported from `app.services.test_generation` together with `generate_test_suite` and `build_case_id`. Differences from the proposal: `GeneratedTestCase` has three additional fields. `expected_outcome` is `response_contract_unasserted` for positive cases and `request_rejection_status_unspecified` for negative cases (negative cases do not assert an HTTP status). `violated_constraint` is `None` for positive cases; for negative cases it names the violated rule (for example `required_parameter`, `required_property`, `required_request_body`, `type`, or the violated schema keyword such as `minimum`, `enum` or `minItems`). `affected_property` is a JSON Pointer relative to the request body for property-level negative cases. `category` is only `positive` or `negative`; the kind of violation is in `violated_constraint` and is also part of the variant that feeds `case_id`. `case_id` is `case-` plus a SHA-256 digest of the length-prefixed category, path, method and variant. Keep request values JSON-compatible and represent parameter location separately; do not serialize query/header/path values into a URL in the generator.

`GeneratedParameterValue.provenance` contains stable source JSON Pointers and, for referenced declarations, the local reference URIs followed. The tuple order is deterministic and describes where a value came from without duplicating the original parameter model. A generator diagnostic carries a stable `code`, endpoint path/method when known, and the most specific source pointer/reference available; its message is explanatory rather than an identifier.

`GeneratedBody | None` distinguishes no body from a present body whose JSON value is `null`: `None` means the generated request has no body, while `GeneratedBody(media_type="application/json", value=None)` means an explicit JSON `null` body. The latter is a positive candidate only when the selected media/schema contract permits null.

`ResponseExpectation` preserves the declared status key and media-type/schema contract, not an assertion that a particular response will occur. `schemas_by_media_type` maps each response media type to its `Schema` when present, retaining raw schema keywords and nested reference edges; `None` means that media type declares no schema. `response_reference_uri` preserves a top-level response edge when the expectation came from a referenced Response Object. A later execution engine matches the observed status against exact, class (`2XX`), or `default` response definitions and evaluates the matching contract.

**Implementation status (`ResponseExpectation`):** *Implemented* for status keys (sorted as strings), media types, schemas as declared, and `response_reference_uri` (the first reference followed). Response headers and links are *not implemented*: `ResponseExpectation` has no fields for them.

The suite should preserve operation and variant order deterministically. `case_id` must be derived from stable input values and variant identifiers, never process hash values or random numbers. Duplicate `operationId` values must not collide; path plus method is the canonical endpoint identity.

## 5. Initial deterministic test categories for MVP

1. **Positive request contract cases:** one valid baseline for each supported operation and selected request media type, with required parameters/body fields populated.
2. **Required parameter cases:** omit one required query/header/cookie parameter at a time. Path parameters cannot be omitted from a concrete request template; a missing path placeholder is instead a generation error/diagnostic.
3. **Required request body/property cases:** omit a required body entirely when applicable; for object payloads, omit one required writable property at a time.
4. **Primitive type cases:** where schema shape is directly supported, generate one deterministic wrong-type value for string, number, integer, boolean, array, and object constraints. `null` is not “wrong type” if allowed by dialect/schema.
5. **Enum/const cases:** generate valid enum/const examples and a deterministic out-of-set value when it can be chosen without collision with allowed values.
6. **Boundary cases:** minimum and maximum plus adjacent invalid values for supported numeric and length/item-count limits. Avoid duplicate cases when bounds coincide or types cannot represent an adjacent value. *Partial:* the Generator creates only invalid boundary violations (a value just outside a limit: `minimum`/`maximum` and exclusive bounds, `minLength`/`maxLength`, `minItems - 1` / `maxItems + 1`). Valid boundary-edge cases (exactly at a limit) are *Planned* and not implemented.
7. **Response contract expectations:** preserve every response status key and media-type/schema expectation in each case’s expected-contract metadata. Generating a request does not imply that all response alternatives can be forced. *Implemented*, without response headers and links (not implemented).

**Implementation status (categories):**

1. Positive cases: *Partial.* One baseline per operation; only `application/json` request bodies and direct primitive parameter schemas are supported (see §7).
2. Required parameter cases: *Implemented* (one omission case per required query/header/cookie parameter, including referenced parameters). A missing path placeholder gives `missing_path_parameter` and no case.
3. Required body/property cases: *Implemented* for a required request body and for required writable object properties. An optional request body gets no omission case.
4. Primitive type cases: *Implemented* (`violated_constraint` is `type`).
5. Enum/const cases: *Implemented*.
6. Boundary cases: *Partial* (invalid violations only, as noted above): numeric bounds, string length and array size (`minItems`/`maxItems`), plus `uniqueItems` duplicates (one case with an array of equal independent copies of one valid item; no distinct item values are generated). Array cases cover only arrays already present in the positive body; array elements and nested arrays are *Planned*.
7. Response expectations: *Partial* (no headers or links).

For the MVP, one baseline and a bounded, explicit set of negative variants are preferable to a Cartesian product of every parameter, property, enum value, auth alternative, body media type and boundary. A documented cap prevents combinatorial growth. Any cap is deterministic, with a stable truncation diagnostic.

**Implementation status (limits):** *Implemented* as private constants in `generator.py`, counting work only (no time or memory limits): 10,000 resolved `$ref` expansions per endpoint and 100,000 per suite; 1,000 cases per endpoint and 50,000 per suite; 100 diagnostics per endpoint and 5,000 per suite. Each generation step is all-or-nothing: when an expansion or case budget is exhausted, the running step is dropped as a whole (none of its cases are kept, and they are not counted), and processing stops according to the budget scope: the rest of the endpoint for an endpoint budget, all remaining endpoints for a suite budget. The stop is reported with `generation_expansion_budget_exceeded` or `generation_case_budget_exceeded`. A step is never truncated partially. Diagnostics above the endpoint or suite limit are replaced by one `generation_diagnostic_budget_exceeded` summary. An unsupported schema in one negative step is reported and the later negative steps still run; a failing positive baseline skips the endpoint.

## 6. Rules for deterministic valid and invalid data

### Determinism

- Use fixed, documented baseline values (for example a fixed string, integer, boolean, array, and object) and canonical ordering of schema properties and component maps.
- Preserve declared array order where OpenAPI gives it meaning: enum order, composition branch order, security alternatives, and scope tuples.
- Do not depend on Python’s randomized hash ordering, wall-clock time, UUID generation, environment variables, network responses, or random libraries.
- Use the following candidate precedence for a positive baseline, stopping at the first candidate that passes the supported schema-subset checks: (1) schema `const`; (2) schema `enum` values in declaration order; (3) a contextual example, if present; (4) schema `example`, then schema `examples` in array order; (5) schema `default`; (6) a canonical constructed fallback. `const` and `enum` jointly constrain eligibility: a `const` candidate must also satisfy `enum`, and every enumerated candidate must equal `const` when one is declared. A conflicting example/default is skipped.
- For a Parameter, contextual examples are ordered as parameter-level `example`, then parameter-level `examples` by sorted example name. For a request/response Media Type, use media-level `example`, then named media `examples` by sorted name. When the candidate source is an Example Object, use its `value`; a reference must first be resolved locally under the reference policy. A candidate with no directly available value is skipped with a diagnostic if no later candidate succeeds. Treat an explicit JSON `null` as a candidate only when source presence is distinguishable from absence.
- **Implementation status (example candidates):** *Implemented in code, partial; not yet covered by dedicated tests (see the last sentence).* Parameter and Media Type examples are collected in the order above (`example`, then named `examples` by sorted name, using the Example Object `value`) ahead of schema `example`, `examples` and `default`, and a candidate that does not satisfy the supported schema subset is skipped. However, an Example *reference* (`unsupported_example_reference`) or an inline Example Object without a usable `value` (`unsupported_example_value`) fails the positive generation step, and the endpoint is skipped with that diagnostic, even if a later candidate would have been valid. Falling back to a later candidate in that case is *not implemented*. *Test coverage:* this failure behavior is documented from the source code only. No dedicated test asserts `unsupported_example_reference` or `unsupported_example_value`, so it is not yet verified by tests.
- If both a Parameter and its schema provide candidates, apply the Parameter candidates before schema `example`/`examples`/`default`, but after the schema’s `const`/`enum` restrictions. If both a Media Type and its schema provide candidates, apply Media Type candidates at the same contextual-example position. Do not merge candidate sets implicitly.
- Validate every selected candidate against all constraints in the supported schema subset before using it as a positive value. Examples and defaults are annotations and are not presumed valid. If an unevaluated assertion keyword could change validity, do not label the candidate schema-valid; emit an unsupported-schema diagnostic or generate only a clearly scoped case.
- For invalid cases, alter exactly one property/constraint from a valid baseline where possible, so the intended failure is attributable.

**Current presence limitation:** `Parameter.example` and `MediaType.example` are typed as `JsonValue | None`, and the API Model contract expects field-set information to distinguish absent from explicit JSON `null`. However, the current Normalizer passes `source.get("example")` for both absent and present fields, so both arrive as explicitly set `None`. Before the generator relies on this distinction, the Normalizer must preserve source presence (for example, pass the field only when the key exists) or the contract must choose a different representation. Until then, treat these `None` values as absent; explicit-null candidates remain available in raw `Schema` and named Example Object `value` fields where their presence can be preserved.

### MVP-supported schema subset

Begin with boolean schemas and direct primitive/object/array schemas where the relevant keywords are understood: `type`, `properties`, `required`, `items`, `enum`, `const`, numeric bounds and `multipleOf`, string `minLength`/`maxLength`, array `minItems`/`maxItems`, `uniqueItems`, and direction-specific `readOnly`/`writeOnly`. Preserve but do not claim generation support for all other keywords.

**Implementation status (supported subset):** *Implemented, partial.* The Generator accepts `type`, `nullable` (OpenAPI 3.0 only), `enum`, `const`, `example`, `examples`, `default`, `minimum`, `maximum`, `exclusiveMinimum`, `exclusiveMaximum`, `multipleOf`, `minLength`, `maxLength`, `minItems`, `maxItems`, `uniqueItems`, `properties`, `required`, `items`, `readOnly`, `writeOnly` (accepted and type-checked as a boolean only; no response-side semantics), `title`, `description`, `deprecated` and `x-` keywords. Any other keyword gives `unsupported_schema_keyword` at its pointer, and `allOf`/`oneOf`/`anyOf`/`not` give `unsupported_schema_composition`. Boolean schemas follow the document version (not accepted for 3.0; `true` accepts any value and `false` has no positive value). Parameters accept direct primitive schemas only.

- Treat `required` as belonging to the containing object, not as a property constraint. Required fields need deterministic values recursively.
- Keep optional properties omitted in the baseline unless selected for an optional-property variant.
- For `readOnly: true`, do not generate that property in request bodies; for `writeOnly: true`, do not expect it in response schemas. Apply requiredness with the OpenAPI direction rules and report cases where the model’s raw schema lacks enough context for a safe interpretation. *Status:* the `readOnly` request-body rule is *implemented* (read-only properties are not generated and get no negative cases). The `writeOnly` response rule is *not implemented*: response schemas are kept as declared and are not evaluated, and `writeOnly` is only type-checked.
- Respect OpenAPI version: in 3.0, interpret `nullable: true` in its allowed `type` context; in 3.1, recognize `type: ["…", "null"]` or composition permitting null. Do not equate the source forms or erase dialect. *Status: implemented.* `nullable` is accepted only for 3.0; a `nullable` keyword in 3.1, or a `null` type in 3.0, is unsupported. Only one non-null type per `type` list is supported.
- Preserve `format`, `pattern`, and all unknown keywords. The initial generator should only produce format/pattern cases for a small declared deterministic support table. Unsupported formats or regex patterns receive diagnostics rather than guessed values. Regex validity checking should not be invented from string generation alone. *Status:* no support table exists. `format` and `pattern` are currently *unsupported*: a schema that the Generator must interpret and that uses either keyword gives `unsupported_schema_keyword`, and no value is generated from it. A small support table is *Planned*.
- Validate generated candidates against only the explicitly implemented subset. Do not add a second complete JSON Schema/OpenAPI validator under the guise of generation.
- Do not flatten `allOf`, `oneOf`, `anyOf`, or `not`. Until a dialect-aware composition evaluator is implemented and tested, report composed schemas as unsupported for body-value generation (or support only a separately specified safe subset). In particular, `oneOf` requires proving exactly one branch matches; choosing an arbitrary branch is insufficient. *Status: unsupported* (`unsupported_schema_composition`, no cases).

## 7. Required and optional inputs

### Parameters

Use the effective ordered `Endpoint.parameters` list. For a `ParameterReference`, resolve a concrete component parameter only on demand; preserve the reference URI and `source_scope` in provenance. References are already considered during normalization’s `(name, in)` merging, but their schemas and requiredness are not copied onto the endpoint model.

- `path` parameters are required by OpenAPI. Extract each `{name}` path template placeholder and require a matching effective parameter; report an unsupported/malformed operation if it is absent. Do not silently leave braces in a generated path value.
- Required query/header/cookie parameters are present in positive cases. Optional ones are omitted by default; optional-included variants can be deferred to avoid case explosion.
- Negative required-parameter cases omit one required non-path parameter at a time. For path parameters, a future request serializer may represent missing/invalid path substitution as a dedicated invalid-request case, but the MVP should not emit an invalid URL string accidentally.
- Preserve `style`, `explode`, `allow_reserved`, and the distinction between schema-based and content-based parameters. The output initially stores logical parameter values; wire serialization is an execution/serialization layer responsibility. Restrict generation to styles whose rules have dedicated tests and issue diagnostics for unsupported serialization combinations. *Status:* storing logical values only is *implemented*. `style`, `explode` and `allow_reserved` are not inspected, and diagnostics for unsupported serialization combinations are *not implemented*. Only direct primitive parameter schemas are accepted, so no complex serialization is generated; a parameter with `content` or a non-primitive schema gives `unsupported_parameter_schema` and the endpoint is skipped.

### Request bodies

- `request_body is None`: no request-body declaration.
- `required=False`: a body is declared but may be omitted; the baseline may omit it, while a separate positive body-present case may be emitted. *Status:* the current baseline always includes a declared body, even when `required=False`. No omission case is generated for an optional body, and no separate body-present variant exists (*Planned*).
- `required=True`: the positive request includes a body value for one supported declared media type; missing-body negative cases can be generated. *Status: implemented* (one omission case).
- Preserve all declared media types. Generate one bounded baseline per selected supported media type rather than silently choosing JSON. Opaque multipart, form, XML, and binary encodings remain unsupported until their serialization rules are implemented. *Status: partial.* Only `application/json` is supported, and it must be the only declared request media type. Any other media type, or `application/json` together with another one, gives `unsupported_request_media_type` and the entire endpoint is skipped. Media Type `encoding` gives `unsupported_request_encoding`. Other media types are *unsupported*.
- Follow a local Request Body component reference on demand with cycle detection. A missing target, unsupported chain or cycle yields an explicit diagnostic and no misleading case. *Status: implemented* (`unsupported_request_body_reference`, alias chains included).
- In a JSON body, generate required writable properties, omit optional fields in the baseline, and create one-property omission variants where supported.

## 8. Response expectations

The generation engine has no observed response and must not claim that it has tested a response. It should attach the operation’s declared response contract to each request case or produce separately addressable response-contract cases.

- Keep status keys as strings. At execution time, match an exact status first, then the corresponding `NXX` range, then `default`.
- Preserve every response definition, media type, schema edge, headers, links, and response references in output/provenance where modeled. *Status: partial.* Status keys, media types, schemas as declared and the top-level response reference URI are kept. Response headers and links are *not implemented*.
- For a local response reference, follow `Components.responses` lazily, with the same cycle/missing-target policy. Do not expand unrelated responses. *Status: implemented* (alias chains included; each resolved hop is charged to the expansion budget). A response reference that is missing, cyclic or outside `#/components/responses/` gives `unsupported_response_reference` and skips the entire endpoint (no positive or negative case for it); other endpoints are unaffected.
- Response schema validation is a future execution assertion. The generator may define expected structural constraints now, but it cannot confirm a live response without execution.
- Preserve the difference between a concrete response with no content and a response reference that has not yet been inspected.

## 9. Security and server handling

The model preserves security meaning but contains no credentials. Resolve effective requirements as follows: operation `security is None` inherits root `ApiModel.security`; an explicit operation `()` disables inherited requirements; an empty requirement object represents an anonymous alternative; different requirement objects are OR alternatives and schemes within one object are jointly required. A missing global declaration means no declared security requirement. The generated case should carry scheme names/scopes only. Actual tokens, passwords, certificates and API keys are supplied later by an execution configuration outside the suite/model.

For security scheme references, follow `Components.security_schemes` only to inspect the declared mechanism. If unresolved or cyclic, report it. Do not fabricate a credential or claim authentication was exercised without execution credentials. *Status:* *Implemented, partial.* For every scheme named by the effective requirements the Generator looks the name up in `Components.security_schemes`, follows `#/components/securitySchemes/<name>` alias chains (cycle detection, one expansion budget unit per hop) and checks the concrete type (`apiKey`, `http`, `oauth2`, `openIdConnect`; `mutualTLS` only for OpenAPI 3.1). A missing name or an invalid, missing or cyclic reference gives `unsupported_security_scheme_reference`; an unsupported type gives `unsupported_security_scheme`. All failures of the endpoint are reported together, in requirement order and then sorted name order (identical pointer and reference URI once), and the whole endpoint is skipped (no alternative is dropped). An exhausted expansion budget is not such a failure and keeps its own `generation_expansion_budget_exceeded` diagnostic. Anonymous requirements and an explicitly empty `security` look nothing up. Type-specific required fields (`apiKey` `name` and `in`, `http` `scheme`, `oauth2` `flows`, `openIdConnect` `openIdConnectUrl`) and the OpenAPI 3.1-only `mutualTLS` type are validated by the OpenAPI Parser through `openapi-spec-validator`, covered by real-validator tests in `tests/test_openapi_parser.py`; the Generator intentionally does not repeat those checks, and its responsibility is reference resolution and supported scheme type validation. Cases still carry only the effective requirements (names and scopes); resolved scheme metadata in cases and OAuth2 scope validation are *not implemented*. Effective requirement inheritance is *implemented*.

Root, Path Item and Operation server declarations remain separate in `ApiModel`/`Endpoint`. Generation should not turn spec server URLs into network targets. Selecting a configured base URL, applying server-variable values, environment overrides, SSRF protection and request allowlists belong to execution/configuration. Explicitly empty and absent server tuples must remain distinguishable until the target policy is applied.

## 10. Unsupported OpenAPI features and explicit boundaries

Initial implementation should explicitly report or skip with a diagnostic, never silently approximate:

- external references, JSON Schema `$dynamicRef`/`$dynamicAnchor`, unsupported anchors, unresolved local references and reference cycles;
- `allOf`, `oneOf`, `anyOf`, `not`, or nested composition forms until matching and value construction preserve their actual semantics;
- unsupported custom JSON Schema dialects/vocabularies and unknown keywords for which no generation rule exists;
- `pattern` and `format` values without a tested deterministic generator/validator rule;
- multipart, form-urlencoded, XML, binary, streaming, file upload and complex `encoding` serialization;
- unsupported parameter `style`/`explode` combinations;
- callbacks, webhooks, links as executable flows, and `Components.other` categories as generation instructions;
- server selection and variable substitution, target credentials, cookies managed by an auth session, and live network behavior;
- stateful workflows, cross-operation data dependencies, retries, load/performance tests, fuzzing, property-based random exploration and AI-derived cases.

Boolean schemas are represented by the model and may be handled directly: `true` permits any JSON value (still subject to limits); `false` has no positive value and can only support an intentionally unsatisfiable/negative case with an explicit diagnostic. Parser validation and OpenAPI version determine whether a boolean schema is legal in the source document.

## 11. Proposed module structure

Keep generation separate from OpenAPI parsing/normalization but within the existing services layer:

```text
backend/app/services/
├── openapi/
│   ├── models.py                 # existing normalized API contract
│   ├── parser.py                 # existing parse + specification validation
│   └── normalizer.py             # existing validated dict -> NormalizationResult
└── test_generation/
    ├── __init__.py               # public generation function [exists]
    ├── models.py                 # generated suite/case/request/expectation/diagnostic types [exists]
    ├── generator.py              # [exists] orchestration, case IDs, value construction, reference lookup, budgets
    ├── values.py                 # [proposed, NOT created] deterministic supported-schema value construction
    └── references.py             # [proposed, NOT created] cycle-aware local lookup over existing components
```

Start with only `generator.py`, `models.py` and focused private helpers if the implementation remains small. Split `values.py` and `references.py` when those responsibilities become independently testable; do not introduce a generic plugin, rule registry, serializer or framework prematurely. A distinct `GenerationError` may be useful for invalid generator input, while unsupported-but-reportable constructs belong in output diagnostics.

**Implementation status (modules):** Only `__init__.py`, `models.py` and `generator.py` exist. `values.py` and `references.py` have not been created and remain part of the proposed architecture; the corresponding behavior (deterministic value construction and lazy local reference lookup) is currently located in `generator.py`. `GenerationError` has not been introduced: unsupported constructs are reported as generation diagnostics, and an input that is not a `NormalizationResult` raises `TypeError`.

## 12. Testing strategy

Tests should be offline, deterministic, and use constructed `NormalizationResult`/`ApiModel` fixtures. Parser tests remain responsible for parsing/validator behavior; generator tests must not rerun full OpenAPI validation.

Cover at least:

- same normalized model yields byte-for-byte equivalent serialized suite, case IDs and ordering across repeated calls;
- equivalent dictionary insertion orders yield equivalent output where map order is not semantically meaningful;
- endpoint attribution for each method/path and stable case identity even with duplicate `operationId` values;
- root/path/operation parameter scope and effective override use, parameter references, alias chains, cycles, missing references and same-name/different-location parameters;
- required versus optional parameters, path placeholders, absent/optional/required request body states, required/optional object properties, read/write direction;
- multiple request media types and explicit unsupported encoding/style behavior;
- primitive valid values, nullability by OpenAPI version, enum/const, string/numeric/array boundaries, wrong types, and no duplicate boundary cases;
- schema component lookup, recursive schemas, `$ref` siblings, missing targets, composition diagnostics and boolean schemas;
- reference and global/operation security distinctions, alternatives, multiple schemes and anonymous cases, with no credential fields in generated models;
- response exact/range/default keys, referenced responses and multiple response content types;
- output limits and deterministic truncation diagnostics;
- input-model immutability and safe behavior on deeply nested/cyclic reference graphs (model JSON data itself is acyclic; schema references can form logical cycles).

Every supported generation rule needs positive and negative examples. Unsupported cases need assertions that the engine emits a clear diagnostic and does not emit a plausible but invalid case. Keep expected fixtures small and assert meaningful payloads, not only case counts.

## 13. Implementation stages

1. **Freeze the boundary/output contract:** choose the generated suite/case fields, diagnostic policy, case identity, and limits. Do not modify the API Model for generator-only metadata. *Implemented.*
2. **Introduce output models and empty orchestration:** accept `NormalizationResult`, return a deterministic empty/diagnostic suite for unsupported inputs, and test serialization and stable identifiers. *Implemented.*
3. **Generate one positive case per simple operation:** support concrete inline primitive parameters and simple JSON bodies; skip/report unresolved references and unsupported schemas. *Implemented* (`application/json` bodies and direct primitive parameters only).
4. **Add local component lookup:** support parameter/request-body/response/schema pointer references on demand, cycle/missing-target diagnostics, and provenance. *Implemented* for parameters, request bodies, responses, schemas and security schemes.
5. **Add requiredness and negative cases:** omit required non-path parameters, bodies and object properties one at a time; retain optional fields’ omission behavior. *Implemented* (required parameter, required body and required property omission; an optional body gets no omission case).
6. **Add enums, const and boundaries:** implement only the explicitly tested scalar/array constraints and a hard deterministic case cap. *Implemented* for enum/const, numeric, string-length, array-size and `uniqueItems` violations, with deterministic budgets in place of a single cap. Valid boundary-edge cases are *Planned*.
7. **Add security and response expectation metadata:** preserve inheritance/anonymous semantics and response matching rules without credentials or execution. *Partial:* effective security inheritance, security scheme validation (Generator: type and local references; Parser: required fields) and response expectations (status keys, media types, schemas, response reference URI) are implemented; resolved scheme metadata in cases and response headers/links are not.
8. **Expand dialect/serialization support:** add only tested 3.0/3.1 null behavior, formats, patterns, media encodings and parameter serialization styles; report the rest as unsupported. *Planned* (not started; `format`, `pattern`, other media types and `style`/`explode` handling are currently unsupported).
9. **Integrate later with execution:** a separate task defines target selection, credential injection, request serialization, network safety, response assertion execution and result collection. *Planned* (not started; belongs to the Test Execution Engine, Phase 5).

Each stage should have isolated tests and preserve the same input contract. Do not claim coverage for a feature until both its generation rule and its invalid/unsupported behavior are tested.

## 14. Main gaps and decisions before implementation

- `Schema.raw` preserves semantics but supplies no typed schema traversal/evaluation API; generator-owned helpers must interpret a small subset without altering the API Model.
- Effective endpoint parameters intentionally retain reference edges. The generator must resolve referenced parameter definitions to obtain `required`, schema/content and serialization fields while preserving the endpoint edge and provenance.
- `LocalReference` validates local URI fragment syntax, but generator lookup support and anchor semantics are not established by the model. Define a supported URI-to-registry subset; the installed parser validator currently has a tested limitation for OpenAPI 3.1 anchor resolution.
- Compositions are preserved but not evaluated. Supporting `oneOf` correctly requires proving exactly one branch, not simply choosing a branch.
- `format` and `pattern` are preserved but no deterministic support registry exists. Start with a small explicit set or report them unsupported.
- Response definitions represent possible contracts, not guaranteed status outcomes. The execution layer must match actual status to exact/range/default keys and perform assertions.
- Target server selection and credential provisioning are intentionally absent. The generator should emit logical cases independent of environment and target secrets.
- `API_MODEL_NORMALIZER_PROPOSAL.md` records the historical pre-implementation design; current implementation facts are in the Normalizer source, API Model contract, and tests.

**Status of these gaps (after Steps 1–6.3):**

- Typed schema interpretation: resolved for a small keyword subset by private helpers in `generator.py`; the API Model is unchanged.
- Referenced parameter definitions: resolved on demand, with the original endpoint entry kept as provenance.
- Local reference support: the supported `#/components/...` forms are defined in §3; anchors remain unsupported. Schema references are resolved on the request side only; response schemas are not resolved.
- Compositions: still unsupported.
- `format` and `pattern`: still unsupported; no support table exists.
- Response contracts, target server selection and credentials: unchanged (execution layer / not generated).

Stages 1–6 above are implemented, so the earlier recommendation to approve and implement them no longer applies. Any further work should be taken from the items labelled *Planned* or *Unsupported* in this document and in `docs/ROADMAP.md`.
