# API Model / Normalizer — architecture proposal

**Scope:** This is a design review only. No files were modified and no dependencies were installed. Findings below are based on the local repository files; they distinguish implemented behavior from documented plans.

## 1. Summary of the current implementation

The OpenAPI service currently has one public entry point: `parse_openapi_document(content, filename) -> dict[str, Any]`, exported with its parser exceptions from the package. It returns the parsed, validated source document as a dictionary; it does not create a richer model.

The parser:

- Accepts JSON and YAML selected by filename extension.
- Applies a 5 MiB limit, rejects unknown extensions, and converts malformed input to predictable parse errors.
- Uses a `SafeLoader` subclass that rejects YAML aliases.

The validator:

- Checks that the root is a dictionary and checks the OpenAPI version format and supported 3.0.x/3.1.x families.
- Rejects invalid or external `$ref` values before calling `openapi-spec-validator`.
- Calls the validator and translates its relevant failures into the project’s validation exception.

The `$ref` check covers `$ref` values. The source does not show a corresponding check for every URI-like or JSON Schema reference keyword, such as `$dynamicRef`; that is a specific area to assess before claiming broader external-reference blocking.

No current module extracts operations, merges parameters, creates component registries for consumers, or builds endpoint/schema entities. References are not expanded into a normalized model. See [parser.py](</D:/PETS/api-sentinel/backend/app/services/openapi/parser.py>), [validator.py](</D:/PETS/api-sentinel/backend/app/services/openapi/validator.py>), and [the package exports](</D:/PETS/api-sentinel/backend/app/services/openapi/__init__.py>).

The HTTP layer remains separate: [main.py](</D:/PETS/api-sentinel/backend/app/main.py>) includes the health router; [health.py](</D:/PETS/api-sentinel/backend/app/api/routes/health.py>) provides only `/` and `/health`; [config.py](</D:/PETS/api-sentinel/backend/app/config.py>) contains basic Pydantic Settings configuration. The parser is not wired to an upload route or application lifecycle.

## 2. Repository-specific findings

### Existing interfaces and boundaries

- **Public parser interface:** `parse_openapi_document(content, filename) -> dict[str, Any]`.
- **Parser errors:** `OpenAPIParserError`, `OpenAPIParseError`, `UnsupportedOpenAPIVersionError`, and `OpenAPIValidationError`.
- **Current service boundary:** `backend/app/services/openapi/`; `backend/app/services/__init__.py` exists.
- **No Normalizer interface or API model exists.**
- **No upload endpoint exists.** Parsing is currently a callable service, not an HTTP workflow.

### Dependencies

The pinned requirements are in [requirements.txt](</D:/PETS/api-sentinel/requirements.txt>):

- `fastapi[standard]==0.162.0`
- `pydantic-settings==2.16.2`
- `pytest==9.2.0`
- `httpx==0.28.1`
- `openapi-spec-validator==0.9.0`
- `PyYAML==6.0.3`

`openapi-spec-validator` validates OpenAPI documents; it does not provide the project’s endpoint extraction or stable API model. PyYAML handles YAML parsing. Pydantic is installed transitively and used by FastAPI and Pydantic Settings; `pydantic` is not directly pinned in the requirements file. If the Normalizer’s public model contract is implemented with Pydantic, it would be clearer to declare Pydantic as a direct dependency and pin it under the project’s dependency policy.

### Existing validation guarantees and limits

Current guarantees include format parsing, document size and extension checks, safe YAML loading, rejection of YAML aliases, version checks, a root-object check, `$ref` filtering, and specification validation through the installed validator.

The validator’s actual behavior also has a known limitation: the repository’s current tests show that a valid-looking OpenAPI 3.1 component `$anchor` reference raises `Unresolvable` from the installed validator. The limitation is captured in [test_openapi_parser.py](</D:/PETS/api-sentinel/tests/test_openapi_parser.py:211>). The local reference syntax filter accepting an anchor does not mean the installed specification validator can resolve that anchor.

### Current test coverage

The current suite has 47 tests from the latest recorded full run: three health tests and parser tests. Parser tests cover JSON/YAML success and syntax failures, empty input, missing fields, versions, structural failures, filename extensions, unsafe YAML tags and aliases, external and malformed `$ref` values, local-reference filtering, a real-validator JSON Pointer case, the anchor limitation, deep reference traversal, invalid Unicode, and oversized input. They do not test endpoint extraction or normalized entities because those are not implemented.

### Documentation and implementation discrepancies

[ARCHITECTURE.md](</D:/PETS/api-sentinel/docs/ARCHITECTURE.md:64>) currently assigns endpoint discovery, parameter extraction, request/response schema extraction, authentication extraction, reference resolution, and normalized representation to the **Specification Parser**. Those responsibilities are proposed in the document, not present in the implementation. Its data-flow section also says the Parser produces the normalized API representation.

By contrast, [PRODUCT_REQUIREMENTS.md](</D:/PETS/api-sentinel/docs/PRODUCT_REQUIREMENTS.md:44>) and [ROADMAP.md](</D:/PETS/api-sentinel/docs/ROADMAP.md:67>) state API discovery and normalization as requirements or planned Phase 3 work. They should remain explicitly marked as planned until implemented. `TECH_STACK.md` and `PROJECT_OVERVIEW.md` label Schemathesis as proposed; it is not a current dependency.

**Documentation recommendation:** describe the current Parser as decoding and validating a document, then assign extraction and model construction to a distinct Normalizer. Update the architecture flow to show validated document → normalized API model → future Test Generator.

## 3. Recommended internal data model

For the Basic Edition, I recommend Pydantic v2 models as the contract between the Normalizer and the future Test Generator. Pydantic is already present in the environment and established in the project’s settings layer; its model validation and serialization suit a typed contract. Keep the models focused, use tuples for ordered collections where appropriate, and avoid turning every JSON Schema keyword into a bespoke Python field.

A `frozen=True` Pydantic model is only shallowly frozen; nested dictionaries still need disciplined copying or an immutable representation if deep immutability becomes necessary. The MVP can use models with copied JSON-compatible values and a documented no-mutation contract.

| Entity | Responsibility and important fields | Relationships and preservation |
|---|---|---|
| **API** | Document-level contract: OpenAPI version, API title/version/description/contact/license, servers, global security, endpoints, and component registries. | Owns endpoints and component catalogs. Preserve document dialect information such as `jsonSchemaDialect` when present. |
| **Endpoint** | One operation: path template, HTTP method, `operationId`, summary, description, tags, deprecated flag, effective parameters, request body, responses, security, and effective servers. | Belongs to API. Preserve operation metadata and distinguish inherited values from operation overrides where useful. |
| **Parameter** | Name, location (`path`, `query`, `header`, `cookie`), required, description, deprecated, schema or content, style, explode, allowReserved, examples, and default. | Attached to an Endpoint. Preserve serialization settings; generator behavior depends on them. |
| **RequestBody** | Description, required flag, and content keyed by media type. | Attached to Endpoint; media types contain the body schema and encoding/examples. |
| **Response** | Original response key (`200`, `2XX`, `default`), description, headers, content, and optionally links. | Endpoint has zero or more responses. Preserve response-key strings rather than coercing all keys to integers. |
| **MediaType** | Media type name plus schema/reference, examples, and encoding. | Used by requests and responses. Do not assume every body is JSON. |
| **SecurityRequirement** | One alternative security requirement: named scheme → scopes. | A list of requirements is OR; schemes within one requirement object are AND. Preserve empty objects and empty arrays because they affect optional or disabled security. |
| **SecurityScheme** | Scheme definition: type, location/name for API keys, HTTP scheme/bearer format, OAuth flows/scopes, OpenID Connect URL, description. | Stored in API components and referenced by security requirements. Never put configured secrets or tokens here. |
| **Server** | URL template, description, and variables/defaults. | API, path, and operation scopes. Preserve original scope and effective override result. |
| **Schema** | A schema node whose source JSON Schema/OpenAPI keywords are preserved, with a small typed reference handle for component lookup. | Used by parameters, bodies, media types, and components. Keep composition and references intact; do not flatten. |

For `Schema`, the MVP should preserve the raw JSON-compatible schema object as the source of truth and expose a small set of typed accessors for common test-generation needs: type, format, enum/const, required, properties, items, defaults, read/write flags, and known constraints. Keep a component schema registry keyed by canonical local reference. Do not duplicate a full schema dictionary into many parallel fields that can drift out of sync.

Useful model-level distinctions include:

- `Endpoint.security is None`: inherit API-level security.
- An empty security list: explicitly disable inherited security.
- A list containing an empty requirement object: anonymous access is one accepted alternative.
- Store both effective servers/parameters and their source scope or provenance when overrides occurred.

## 4. Reference handling strategy

| Approach | Correctness | Recursive schemas | Memory and generation | Maintenance and source fidelity |
|---|---|---|---|---|
| **Expand every reference fully** | Tempting for simple consumers, but risks changing `$ref` sibling semantics or mishandling dialect behavior. | Can loop or duplicate unboundedly. | Can grow substantially; generator loses clean component identity. | Hard to preserve provenance and source structure. Not recommended. |
| **Keep references and a component registry** | Preserves reference boundaries and source semantics. Consumers must perform lookup deliberately. | Naturally represents recursion as reference edges. | Compact; generator follows references lazily. | Clear separation and good source fidelity. Recommended baseline. |
| **Hybrid: registry plus selective lookup** | Preserves references while making common use convenient. Correct if lookup never silently flattens or changes semantics. | Registry keeps cycles finite; traversal can detect a repeated reference. | Compact, and the generator can resolve only what a test needs. | Slightly more logic, but a practical MVP balance. Recommended. |

**Recommendation:** use the hybrid form. Build a component registry for supported reusable component types; preserve `$ref` as a reference node in endpoints and schema values. The Normalizer may resolve references for tasks that require effective values, such as merging parameter definitions, but should not recursively inline the whole graph. The Test Generator should use a lookup operation with a visited-reference set and a configurable depth/size budget.

The Parser’s existing external `$ref` policy should remain in force; the Normalizer must not fetch references. Before implementation, explicitly review the 3.1 JSON Schema reference keywords that the current filter does not cover, particularly `$dynamicRef`, and decide whether the Parser should reject unsupported forms or the MVP should report them as unsupported. Do not silently treat them as ordinary local `$ref` references.

## 5. OpenAPI semantics the Normalizer must preserve

OpenAPI 3.0 Schema Objects use a restricted schema model with `nullable`; OpenAPI 3.1 aligns Schema Objects with JSON Schema Draft 2020-12, including JSON Schema vocabulary and dialect behavior. The OAS 3.1 specification defines the default dialect and supports JSON Schema keywords such as `$dynamicRef`; 3.0 documents `nullable` and its constraints. [OpenAPI 3.0.3](https://spec.openapis.org/oas/v3.0.3), [OpenAPI 3.1.1](https://spec.openapis.org/oas/v3.1.1.html)

- **Local `$ref` and components:** keep reference strings and the component registry; resolve only local references. Preserve reference-object `summary`/`description` overrides and, for 3.1 Schema Objects, sibling keywords. Do not reinterpret 3.0 and 3.1 `$ref` behavior as identical.
- **`allOf`, `oneOf`, `anyOf`, `not`:** preserve each as composition nodes. `allOf` means every subschema applies; `oneOf` means exactly one applies; `anyOf` means one or more apply; `not` excludes matches. Flattening can change validity, especially with `additionalProperties`, conflicting constraints, or nested references.
- **Nullability:** preserve 3.0 `nullable: true` with its `type` context. In 3.1 preserve a `null` type or a composition that permits null. Do not normalize both forms into one lossy flag.
- **Required and optional properties:** `required` belongs to the containing object schema; a property absent from that list is optional. Preserve required arrays and property schemas independently.
- **Read-only and write-only:** retain both annotations and apply them by direction. For request generation, read-only fields should generally be omitted; for response expectations, write-only fields should generally be omitted. Required status can be direction-dependent for these properties under OpenAPI semantics. The 3.0 specification describes this behavior explicitly. [OpenAPI 3.0.3](https://spec.openapis.org/oas/v3.0.3)
- **Defaults, examples, enums, and const:** preserve values as JSON values with their original types. A default is an annotation; do not assume the target API applies it. `enum` constrains values; in 3.1 preserve `const` as well.
- **Arrays and constraints:** preserve `items`, and in 3.1 keywords such as `prefixItems`; preserve `minItems`, `maxItems`, and `uniqueItems`. Keep `minLength`, `maxLength`, `pattern`, numeric minimum/maximum, exclusive bounds, and `multipleOf`. The 3.0 and JSON Schema 3.1 forms of exclusive numeric bounds differ, so do not coerce them without dialect-aware logic.
- **Formats:** preserve `format` exactly. Format enforcement varies by validator and format; the generator should only generate cases for formats with explicit deterministic generators and report unsupported formats.
- **Request/response content:** preserve every declared media type, schema, example, and request-body encoding. Do not assume JSON or flatten multiple response definitions.
- **Parameter serialization:** retain `style`, `explode`, `allowReserved`, location, and schema/content. Apply OpenAPI defaults by parameter location only when producing an effective normalized view; keep enough source detail to explain the effective serialization. The specification defines different defaults for styles and explode. [OpenAPI 3.0.3](https://spec.openapis.org/oas/v3.0.3)
- **Security:** retain global and operation security separately or preserve explicit inheritance state. A list of requirement objects represents alternatives; schemes within an object are cumulative. An empty requirement permits anonymous access, while an empty operation security array removes global security for that operation. [OpenAPI 3.1.1](https://spec.openapis.org/oas/v3.1.1.html)
- **Parameter inheritance:** combine path-level parameters with operation-level parameters. Operation-level entries override a path-level entry with the same `(name, in)` key; the specification does not allow operation-level removal. Retain provenance for overridden values if useful for diagnostics. [OpenAPI 3.1.1](https://spec.openapis.org/oas/v3.1.1.html)
- **Server inheritance:** operation servers override path servers, which override root servers. Preserve the declared and effective server scopes, including URL variables and defaults. A missing or empty root server list has a defined default; do not treat it as an absent target base URL without documenting that interpretation. [OpenAPI 3.1.1](https://spec.openapis.org/oas/v3.1.1.html)

Recursive schemas should remain finite reference edges into the registry. The Normalizer must not expand them recursively. Generator traversal should track visited references and stop or report when a generation budget is reached.

## 6. Parser and Normalizer responsibilities

### Parser

Keep the current Parser responsible for:

- Input text and filename handling.
- JSON/YAML selection and decoding.
- Document-size and extension checks.
- Safe YAML parsing.
- OpenAPI version checks.
- Specification validation.
- Parser-specific exceptions.
- Returning the validated source dictionary.

It should not extract endpoints or build the normalized model.

### Normalizer

Add a pure service function that accepts a validated document and returns an `ApiModel`. It should:

- Traverse the validated source.
- Extract API metadata and operations.
- Build typed endpoint, parameter, request/response, server, security, media-type, and component entries.
- Preserve raw schema semantics and reference edges.
- Apply defined effective-value rules for parameter/server/security inheritance.
- Produce deterministic output ordering.
- Raise a normalization-specific error for unsupported or internally inconsistent constructs rather than silently dropping them.

It should not decode files, parse YAML, reimplement full specification validation, fetch references, execute requests, or manage target credentials.

### Outside both components

Upload transport limits, authentication/authorization, persistence, environment/base-URL configuration, SSRF controls for future execution, HTTP execution, test generation, and result storage belong to other layers. In particular, server URLs from a spec are data; they must not cause network requests during parsing or normalization.

## 7. Proposed package structure

Keep the Normalizer in the existing OpenAPI service. A separate top-level Normalizer package is not warranted for this MVP.

```text
backend/app/services/openapi/
├── __init__.py
├── exceptions.py
├── parser.py
├── validator.py
├── models.py       # API model and entity types
└── normalizer.py   # validated document -> API model
```

- `models.py`: Pydantic contract models and JSON-compatible schema/reference types.
- `normalizer.py`: deterministic extraction, component indexing, inheritance/override rules, and model construction.
- Existing parser and validator remain focused on decoding and validation.
- `exceptions.py` can gain a distinct normalization exception, or a normalization-specific exception module can be introduced if the exception set grows. Avoid a separate reference framework until implementation complexity justifies it.
- `__init__.py` can export the parser and Normalizer public functions while keeping internal helpers private.

Proposed interface:

```python
normalize_openapi_document(document: dict[str, Any]) -> ApiModel
```

The precondition should be explicit: `document` is already parsed and validated by the Parser. The Normalizer may perform defensive shape checks needed to avoid crashes, but should not duplicate full OpenAPI validation.

## 8. Example transformation

### Example source document

```yaml
openapi: "3.1.1"
info:
  title: Pets API
  version: "1.0"
servers:
  - url: https://api.example.test/v1
security:
  - bearerAuth: []
paths:
  /pets/{petId}:
    parameters:
      - name: petId
        in: path
        required: true
        schema:
          type: string
          format: uuid
    get:
      operationId: getPet
      parameters:
        - name: includeDetails
          in: query
          schema:
            type: boolean
            default: false
      responses:
        "200":
          description: Pet found
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/Pet"
        "404":
          description: Pet not found
  /pets:
    post:
      operationId: createPet
      security:
        - apiKeyAuth: []
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: "#/components/schemas/CreatePet"
      responses:
        "201":
          description: Created
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/Pet"
        "400":
          description: Invalid input
components:
  schemas:
    CreatePet:
      type: object
      required: [name]
      properties:
        name:
          type: string
          minLength: 1
          maxLength: 80
        tag:
          type: string
          nullable: true
    Pet:
      allOf:
        - $ref: "#/components/schemas/CreatePet"
        - type: object
          required: [id]
          properties:
            id:
              type: string
              format: uuid
  securitySchemes:
    bearerAuth:
      type: http
      scheme: bearer
    apiKeyAuth:
      type: apiKey
      in: header
      name: X-API-Key
```

This sample intentionally uses 3.1 with `nullable` only to illustrate preservation of source keywords; in a 3.1 schema, nullability should normally be expressed with JSON Schema types/composition. A production example should use `type: [string, "null"]` or an equivalent composition.

### Normalized representation sketch

```text
ApiModel(
  openapi_version="3.1.1",
  info=ApiInfo(title="Pets API", version="1.0"),
  servers=[Server(url="https://api.example.test/v1", scope="root")],
  global_security=[
    SecurityRequirement(schemes={"bearerAuth": []})
  ],
  security_schemes={
    "bearerAuth": SecurityScheme(type="http", scheme="bearer"),
    "apiKeyAuth": SecurityScheme(type="apiKey", in_="header", name="X-API-Key"),
  },
  components.schemas={
    "CreatePet": Schema(raw={...required, properties, constraints...}),
    "Pet": Schema(raw={"allOf": [
      {"$ref": "#/components/schemas/CreatePet"},
      {...}
    ]}),
  },
  endpoints=[
    Endpoint(
      method="GET", path="/pets/{petId}", operation_id="getPet",
      parameters=[
        Parameter(name="petId", in_="path", required=True, schema={...},
                  source_scope="path"),
        Parameter(name="includeDetails", in_="query", required=False,
                  schema={"type": "boolean", "default": False},
                  source_scope="operation"),
      ],
      security=None,  # inherits global bearerAuth
      responses={
        "200": Response(content={"application/json":
          MediaType(schema_ref="#/components/schemas/Pet")}),
        "404": Response(description="Pet not found"),
      },
    ),
    Endpoint(
      method="POST", path="/pets", operation_id="createPet",
      request_body=RequestBody(required=True, content={"application/json":
        MediaType(schema_ref="#/components/schemas/CreatePet")}),
      security=[SecurityRequirement(schemes={"apiKeyAuth": []})],
      responses={"201": ..., "400": ...},
    ),
  ],
)
```

The example keeps `Pet` composition and request/response references intact. The Generator can look up a schema by reference when it needs to build a request or expected response, without copying the referenced component into each location.

## 9. Model support for deterministic test generation

The model should support these test categories without AI functionality:

| Test category | Required model information |
|---|---|
| Positive tests | Effective parameters, required body state, schema types, formats, enums, examples/defaults, servers, content types, and security scheme names. |
| Negative tests | Required names, types, enum/const values, schema compositions, bounds, parameter serialization, and response contracts. |
| Boundary tests | Numeric minimum/maximum and exclusivity, `multipleOf`, string lengths/patterns, array item counts/uniqueness, and requiredness. |
| Required-field tests | Parent `required` lists, request-body `required`, path parameter requiredness, and direction-aware read/write fields. |
| Data-type tests | Schema types, property/item structure, and 3.1 null unions. |
| Enum tests | Typed enum values and 3.1 `const`; values must not be coerced to strings. |
| Format/constraint tests | `format` and every preserved constraint, with a deterministic support registry in the Generator. |
| Request/response contract tests | Media types, request schemas, status-code response keys, response headers, and response schemas. |

The model should expose enough data to generate test definitions, but generation policy belongs to the Test Generator. For example, the model preserves a regex `pattern`; a separate deterministic generator decides whether it knows how to produce a matching and non-matching value.

## 10. MVP scope and extensibility

### Required for Basic Edition MVP

- Extract all standard HTTP operations under `paths`.
- Build effective parameter lists using path/operation override rules.
- Preserve request bodies, response keys, content types, headers, and media schemas.
- Index reusable schemas, parameters, request bodies, and responses.
- Preserve local references rather than expanding recursively.
- Preserve schema composition and relevant constraints without flattening.
- Preserve security schemes and global/operation requirement semantics.
- Preserve root/path/operation server declarations and effective server choice.
- Return deterministic endpoint/component ordering and normalization errors for unsupported cases.

### Useful but deferrable

- Rich `examples` and `encoding` interpretation for multipart/form-urlencoded bodies.
- Header and response-link modeling beyond basic contract checks.
- Discriminator-based branch selection.
- Support for every parameter serialization style in request generation.
- Full treatment of callbacks, webhooks, and path-item references.
- More elaborate provenance/source maps for UI navigation back to source locations.

### Future extensions or explicit limitations

- External references, unless a separate secure import and resolution policy is designed. Do not alter the Parser’s current policy by implication.
- `$dynamicRef`/`$dynamicAnchor` and arbitrary custom JSON Schema dialects until their reference semantics and validation support are tested end to end.
- Callback/webhook execution, which introduces callback direction and network-security concerns.
- XML, multipart, and uncommon serialization modes in actual request generation.
- Business rules, stateful workflows, and AI-derived scenarios.
- Unsupported schema keywords must be preserved or reported as unsupported; silently discarding them would make generated tests appear more complete than they are.

OpenAPI 3.1 supports a wider JSON Schema dialect than 3.0. The MVP should preserve those source keywords even when the first Generator cannot act on all of them. Unsupported behavior should be explicit in model diagnostics or normalization results, not silently normalized away. See the official [3.0.3](https://spec.openapis.org/oas/v3.0.3) and [3.1.1](https://spec.openapis.org/oas/v3.1.1.html) specifications.

## 11. Risks and edge cases

1. **Reference semantics:** `$ref` behavior differs between Reference Objects and 3.1 Schema Objects. Preserve source version and context.
2. **Anchor validation:** the installed validator currently fails a referenced 3.1 `$anchor`; support should not be advertised as end-to-end until validator behavior is resolved or a supported subset is documented.
3. **Other reference keywords:** current filtering focuses on `$ref`; 3.1 JSON Schema reference keywords and URI-bearing features need an explicit policy.
4. **Recursive schemas:** naïve expansion causes infinite recursion or large duplication. Preserve references and use cycle-aware traversal.
5. **Composition semantics:** flattening `allOf`/`oneOf`/`anyOf` can change schema meaning.
6. **Dialect differences:** `nullable`, exclusive bounds, boolean schemas, `$ref` siblings, and JSON Schema dialects differ between 3.0 and 3.1.
7. **Inheritance semantics:** global versus operation security, path versus operation parameters, and server overrides need tests for absent, empty, and explicit override cases.
8. **Generated values:** `format`, `pattern`, and examples do not guarantee a complete deterministic value generator; make unsupported cases visible.
9. **Source preservation:** if only common keywords are copied into fields, unknown/vendor keywords can disappear. Keep raw schema keywords or an equivalent lossless representation.
10. **Target safety:** server URLs and security definitions in the model are declarative input, not authorization to execute a request. Execution needs a separate SSRF and credential policy.

## 12. Recommended implementation sequence

1. Record the Parser/Normalizer boundary and supported normalization subset in the architecture docs.
2. Define `ApiModel`, `Endpoint`, component registry, and focused child models, including raw schema preservation and reference representation.
3. Define normalization diagnostics and unsupported-feature behavior before extracting fields.
4. Implement metadata, server, component, and security extraction.
5. Implement deterministic path/method endpoint extraction.
6. Implement parameter inheritance/override and effective server/security rules.
7. Implement request/response/media extraction while keeping `$ref` nodes linked to the registry.
8. Add tests per OpenAPI version for references, recursive schemas, compositions, nullability, serialization defaults, and inheritance edge cases.
9. Integrate Normalizer with the Test Generator only after its output contract is stable; keep parsing and normalization independently testable.

The central architectural decision is to treat the Normalizer as a separate pure transformation inside the existing `services/openapi` package. The Parser remains responsible for validated source documents; the Normalizer creates a loss-aware typed model; the future Test Generator consumes that model without depending on YAML parsing or OpenAPI validation internals.
