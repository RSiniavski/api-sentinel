# Phase 4 — Deterministic Test Generation Engine Proposal

**Status:** Architecture proposal based on the current repository. No generation engine is implemented by this document.

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
    -> proposed deterministic generator
    -> declarative generated test cases
    -> future, separate execution engine
```

### Implemented capabilities

- The Parser accepts JSON and YAML, applies a 5 MiB input limit, rejects unsupported extensions and YAML aliases, uses a safe YAML loader, checks OpenAPI version syntax/support, rejects invalid/external `$ref` strings, and invokes `openapi-spec-validator` for specification validation. The supported version families are OpenAPI 3.0.x and 3.1.x.
- The API Model is implemented with strict, frozen Pydantic v2 models. `Schema.raw` preserves JSON-compatible schema objects, including unknown keywords, boolean schemas, compositions and references; construction detaches the input via deep copy. The model does not turn each schema keyword into a typed field.
- The Normalizer is implemented. It extracts API metadata, root servers and security, component registries, and endpoints. It retains references as edges, merges path and operation parameters by `(name, in)`, records override diagnostics, and preserves operation security and server scopes. It rejects unsupported Path Item `$ref` usage rather than resolving it.
- The Normalizer sorts dictionary-backed registries and endpoint paths/methods deterministically while preserving meaningful array order such as security alternatives, tags, and OAuth scopes.
- Existing tests cover parser behavior, API Model invariants and serialization, normalization of metadata/components/endpoints/parameters/security/servers, and health endpoints. There are currently no generation-engine models or tests.

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
| Schema distinctions | Raw content retains `nullable`, null types, `required`, `readOnly`, `writeOnly`, enum/const, primitive constraints, and compositions without normalization. | Semantics are available as source information, but there is no typed constraint evaluator or value generator yet. Dialect must be taken from `ApiModel.openapi_version`. |

### Documentation discrepancy

The current `docs/API_MODEL_CONTRACT.md` introduction and “Current repository facts” still say that the Normalizer has not been implemented. The current source contains `normalizer.py`, exports `normalize_openapi_document`, and has endpoint-normalization tests. The initial `docs/API_MODEL_NORMALIZER_PROPOSAL.md` likewise describes a pre-model state and its early proposed package/interface. Those documents are historical design material and do not accurately state the current implementation status. This proposal uses the source and tests as current evidence and does not modify the existing documents.

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

Add a new, generator-owned output contract rather than adding fields to `ApiModel`:

```python
class GeneratedTestSuite:
    api_title: str
    api_version: str
    openapi_version: str
    cases: tuple[GeneratedTestCase, ...]
    diagnostics: tuple[GenerationDiagnostic, ...]

class GeneratedTestCase:
    case_id: str                 # stable from category + endpoint + variant
    category: str                # e.g. positive, required_field, boundary
    path: str
    method: str
    parameters: tuple[GeneratedParameterValue, ...]
    request_body: GeneratedBody | None
    security: tuple[SecurityRequirement, ...] | None
    response_expectations: tuple[ResponseExpectation, ...]
    provenance: tuple[str, ...]  # stable OpenAPI pointers/references for review

class GenerationDiagnostic:
    code: str
    message: str
    endpoint_path: str | None
    endpoint_method: str | None
    schema_reference: str | None
```

These are proposed types, not existing classes. A concrete implementation may use strict frozen Pydantic v2 models, consistent with the current project, or small dataclasses if serialization is not yet required. Prefer Pydantic if the generated suite will be persisted/exported. Keep request values JSON-compatible and represent parameter location separately; do not serialize query/header/path values into a URL in the generator.

`ResponseExpectation` should preserve the declared status key and optional media-type/schema contract, not assert that a particular response will occur. A later execution engine matches the observed status against exact, class (`2XX`), or `default` response definitions and evaluates the matching contract.

The suite should preserve operation and variant order deterministically. `case_id` must be derived from stable input values and variant identifiers, never process hash values or random numbers. Duplicate `operationId` values must not collide; path plus method is the canonical endpoint identity.

## 5. Initial deterministic test categories for MVP

1. **Positive request contract cases:** one valid baseline for each supported operation and selected request media type, with required parameters/body fields populated.
2. **Required parameter cases:** omit one required query/header/cookie parameter at a time. Path parameters cannot be omitted from a concrete request template; a missing path placeholder is instead a generation error/diagnostic.
3. **Required request body/property cases:** omit a required body entirely when applicable; for object payloads, omit one required writable property at a time.
4. **Primitive type cases:** where schema shape is directly supported, generate one deterministic wrong-type value for string, number, integer, boolean, array, and object constraints. `null` is not “wrong type” if allowed by dialect/schema.
5. **Enum/const cases:** generate valid enum/const examples and a deterministic out-of-set value when it can be chosen without collision with allowed values.
6. **Boundary cases:** minimum and maximum plus adjacent invalid values for supported numeric and length/item-count limits. Avoid duplicate cases when bounds coincide or types cannot represent an adjacent value.
7. **Response contract expectations:** preserve every response status key and media-type/schema expectation in each case’s expected-contract metadata. Generating a request does not imply that all response alternatives can be forced.

For the MVP, one baseline and a bounded, explicit set of negative variants are preferable to a Cartesian product of every parameter, property, enum value, auth alternative, body media type and boundary. A documented cap prevents combinatorial growth. Any cap is deterministic, with a stable truncation diagnostic.

## 6. Rules for deterministic valid and invalid data

### Determinism

- Use fixed, documented baseline values (for example a fixed string, integer, boolean, array, and object) and canonical ordering of schema properties and component maps.
- Preserve declared array order where OpenAPI gives it meaning: enum order, composition branch order, security alternatives, and scope tuples.
- Do not depend on Python’s randomized hash ordering, wall-clock time, UUID generation, environment variables, network responses, or random libraries.
- Define precedence among candidate sources: use a schema’s valid `const`/`enum` candidates first; then use explicit examples/default only when the supported local evaluator confirms they satisfy the implemented subset; otherwise use canonical constructed values. Examples/default are not guaranteed valid merely because they are present.
- For invalid cases, alter exactly one property/constraint from a valid baseline where possible, so the intended failure is attributable.

### MVP-supported schema subset

Begin with boolean schemas and direct primitive/object/array schemas where the relevant keywords are understood: `type`, `properties`, `required`, `items`, `enum`, `const`, numeric bounds and `multipleOf`, string `minLength`/`maxLength`, array `minItems`/`maxItems`, `uniqueItems`, and direction-specific `readOnly`/`writeOnly`. Preserve but do not claim generation support for all other keywords.

- Treat `required` as belonging to the containing object, not as a property constraint. Required fields need deterministic values recursively.
- Keep optional properties omitted in the baseline unless selected for an optional-property variant.
- For `readOnly: true`, do not generate that property in request bodies; for `writeOnly: true`, do not expect it in response schemas. Apply requiredness with the OpenAPI direction rules and report cases where the model’s raw schema lacks enough context for a safe interpretation.
- Respect OpenAPI version: in 3.0, interpret `nullable: true` in its allowed `type` context; in 3.1, recognize `type: ["…", "null"]` or composition permitting null. Do not equate the source forms or erase dialect.
- Preserve `format`, `pattern`, and all unknown keywords. The initial generator should only produce format/pattern cases for a small declared deterministic support table. Unsupported formats or regex patterns receive diagnostics rather than guessed values. Regex validity checking should not be invented from string generation alone.
- Validate generated candidates against only the explicitly implemented subset. Do not add a second complete JSON Schema/OpenAPI validator under the guise of generation.
- Do not flatten `allOf`, `oneOf`, `anyOf`, or `not`. Until a dialect-aware composition evaluator is implemented and tested, report composed schemas as unsupported for body-value generation (or support only a separately specified safe subset). In particular, `oneOf` requires proving exactly one branch matches; choosing an arbitrary branch is insufficient.

## 7. Required and optional inputs

### Parameters

Use the effective ordered `Endpoint.parameters` list. For a `ParameterReference`, resolve a concrete component parameter only on demand; preserve the reference URI and `source_scope` in provenance. References are already considered during normalization’s `(name, in)` merging, but their schemas and requiredness are not copied onto the endpoint model.

- `path` parameters are required by OpenAPI. Extract each `{name}` path template placeholder and require a matching effective parameter; report an unsupported/malformed operation if it is absent. Do not silently leave braces in a generated path value.
- Required query/header/cookie parameters are present in positive cases. Optional ones are omitted by default; optional-included variants can be deferred to avoid case explosion.
- Negative required-parameter cases omit one required non-path parameter at a time. For path parameters, a future request serializer may represent missing/invalid path substitution as a dedicated invalid-request case, but the MVP should not emit an invalid URL string accidentally.
- Preserve `style`, `explode`, `allow_reserved`, and the distinction between schema-based and content-based parameters. The output initially stores logical parameter values; wire serialization is an execution/serialization layer responsibility. Restrict generation to styles whose rules have dedicated tests and issue diagnostics for unsupported serialization combinations.

### Request bodies

- `request_body is None`: no request-body declaration.
- `required=False`: a body is declared but may be omitted; the baseline may omit it, while a separate positive body-present case may be emitted.
- `required=True`: the positive request includes a body value for one supported declared media type; missing-body negative cases can be generated.
- Preserve all declared media types. Generate one bounded baseline per selected supported media type rather than silently choosing JSON. Opaque multipart, form, XML, and binary encodings remain unsupported until their serialization rules are implemented.
- Follow a local Request Body component reference on demand with cycle detection. A missing target, unsupported chain or cycle yields an explicit diagnostic and no misleading case.
- In a JSON body, generate required writable properties, omit optional fields in the baseline, and create one-property omission variants where supported.

## 8. Response expectations

The generation engine has no observed response and must not claim that it has tested a response. It should attach the operation’s declared response contract to each request case or produce separately addressable response-contract cases.

- Keep status keys as strings. At execution time, match an exact status first, then the corresponding `NXX` range, then `default`.
- Preserve every response definition, media type, schema edge, headers, links, and response references in output/provenance where modeled.
- For a local response reference, follow `Components.responses` lazily, with the same cycle/missing-target policy. Do not expand unrelated responses.
- Response schema validation is a future execution assertion. The generator may define expected structural constraints now, but it cannot confirm a live response without execution.
- Preserve the difference between a concrete response with no content and a response reference that has not yet been inspected.

## 9. Security and server handling

The model preserves security meaning but contains no credentials. Resolve effective requirements as follows: operation `security is None` inherits root `ApiModel.security`; an explicit operation `()` disables inherited requirements; an empty requirement object represents an anonymous alternative; different requirement objects are OR alternatives and schemes within one object are jointly required. A missing global declaration means no declared security requirement. The generated case should carry scheme names/scopes only. Actual tokens, passwords, certificates and API keys are supplied later by an execution configuration outside the suite/model.

For security scheme references, follow `Components.security_schemes` only to inspect the declared mechanism. If unresolved or cyclic, report it. Do not fabricate a credential or claim authentication was exercised without execution credentials.

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
    ├── __init__.py               # public generation function
    ├── models.py                 # generated suite/case/request/expectation/diagnostic types
    ├── generator.py              # orchestration and stable case ordering/IDs
    ├── values.py                 # deterministic supported-schema value construction
    └── references.py             # cycle-aware local lookup over existing components
```

Start with only `generator.py`, `models.py` and focused private helpers if the implementation remains small. Split `values.py` and `references.py` when those responsibilities become independently testable; do not introduce a generic plugin, rule registry, serializer or framework prematurely. A distinct `GenerationError` may be useful for invalid generator input, while unsupported-but-reportable constructs belong in output diagnostics.

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

1. **Freeze the boundary/output contract:** choose the generated suite/case fields, diagnostic policy, case identity, and limits. Do not modify the API Model for generator-only metadata.
2. **Introduce output models and empty orchestration:** accept `NormalizationResult`, return a deterministic empty/diagnostic suite for unsupported inputs, and test serialization and stable identifiers.
3. **Generate one positive case per simple operation:** support concrete inline primitive parameters and simple JSON bodies; skip/report unresolved references and unsupported schemas.
4. **Add local component lookup:** support parameter/request-body/response/schema pointer references on demand, cycle/missing-target diagnostics, and provenance.
5. **Add requiredness and negative cases:** omit required non-path parameters, bodies and object properties one at a time; retain optional fields’ omission behavior.
6. **Add enums, const and boundaries:** implement only the explicitly tested scalar/array constraints and a hard deterministic case cap.
7. **Add security and response expectation metadata:** preserve inheritance/anonymous semantics and response matching rules without credentials or execution.
8. **Expand dialect/serialization support:** add only tested 3.0/3.1 null behavior, formats, patterns, media encodings and parameter serialization styles; report the rest as unsupported.
9. **Integrate later with execution:** a separate task defines target selection, credential injection, request serialization, network safety, response assertion execution and result collection.

Each stage should have isolated tests and preserve the same input contract. Do not claim coverage for a feature until both its generation rule and its invalid/unsupported behavior are tested.

## 14. Main gaps and decisions before implementation

- `Schema.raw` preserves semantics but supplies no typed schema traversal/evaluation API; generator-owned helpers must interpret a small subset without altering the API Model.
- Effective endpoint parameters intentionally retain reference edges. The generator must resolve referenced parameter definitions to obtain `required`, schema/content and serialization fields while preserving the endpoint edge and provenance.
- `LocalReference` validates local URI fragment syntax, but generator lookup support and anchor semantics are not established by the model. Define a supported URI-to-registry subset; the installed parser validator currently has a tested limitation for OpenAPI 3.1 anchor resolution.
- Compositions are preserved but not evaluated. Supporting `oneOf` correctly requires proving exactly one branch, not simply choosing a branch.
- `format` and `pattern` are preserved but no deterministic support registry exists. Start with a small explicit set or report them unsupported.
- Response definitions represent possible contracts, not guaranteed status outcomes. The execution layer must match actual status to exact/range/default keys and perform assertions.
- Target server selection and credential provisioning are intentionally absent. The generator should emit logical cases independent of environment and target secrets.
- Existing architecture docs contain stale Normalizer status/interface language. Correcting those documents is a separate documentation task; this proposal does not silently revise their approved content.

Recommended next step: approve the generated suite/case contract and the initial supported schema subset, then implement stages 1–3 with deterministic fixtures before adding reference-heavy and negative-case logic.
