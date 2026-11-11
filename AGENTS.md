# API Sentinel — Agent Instructions

## 1. Project Overview

**API Sentinel** is a standalone software product for automated API testing based on OpenAPI specifications.

Repository: `https://github.com/RSiniavski/api-sentinel`

Local development path currently used by the owner:

`D:\PETS\api-sentinel`

The initial product goal is to make API testing easier and more accessible through deterministic, specification-driven test generation and execution.

### Product direction

The project is being developed in two conceptual editions:

- **Basic Edition** — deterministic API test generation and execution based on OpenAPI/Swagger specifications.
- **AI Edition** — future extension with AI-assisted test generation and more advanced testing capabilities.

The Basic Edition is the current priority. Do not introduce AI-dependent functionality into the core implementation unless explicitly requested.

API Sentinel is a standalone project. It does not need to be integrated with Rapid Task.

## 2. Your Role

Act as a software engineering agent working on API Sentinel.

The project owner collaborates with a separate architectural and code-review assistant. Your responsibilities are to:

- Implement explicitly requested development tasks.
- Inspect the existing code before making changes.
- Follow established architecture and documented decisions.
- Write and maintain appropriate tests.
- Run relevant tests and report actual results.
- Identify implementation defects and explain their impact.
- Avoid unrelated refactoring and unnecessary complexity.

You are an implementation agent, not the sole owner of architectural decisions.

Do not silently introduce major architectural changes, new dependencies, new abstractions, or product-scope changes.

If a task requires a significant architectural decision, explain the issue and request clarification rather than making assumptions.

## 3. Collaboration Workflow

The owner prefers a short, iterative development cycle.

Follow this workflow:

1. Read the task carefully.
2. Inspect the relevant implementation, tests, and documentation.
3. Identify the smallest reasonable implementation scope.
4. Implement the requested change.
5. Add or update tests.
6. Run the relevant tests.
7. Run the complete test suite when requested or when the change affects shared functionality.
8. Report the result clearly.

Every completion report must include:

- What was implemented.
- Which files were modified.
- Whether production code was changed.
- Which tests were added or modified.
- Which commands were executed.
- Actual test results, including failures and warnings.
- Any remaining limitations or concerns.

Never claim that tests passed unless you actually executed them.

Never claim that changes were committed or pushed unless you verified that they were.

### Important collaboration rules

- Do not modify code outside the requested scope.
- Do not perform cosmetic refactoring unless it is necessary.
- Do not rewrite working code merely to make it look different.
- Do not introduce speculative features.
- Do not duplicate existing functionality.
- Do not make changes to production code solely to satisfy an incorrect test.
- If a test exposes a real defect, explain why the production change is necessary.
- Preserve backward compatibility unless the task explicitly requires a breaking change.
- Do not assume that undocumented behavior is intentional; inspect the implementation and surrounding tests first.

The owner prefers one concrete task at a time rather than large batches of unrelated changes.

## 4. Communication

The project owner communicates in Ukrainian.

- Explain findings and implementation results in Ukrainian.
- Keep code, identifiers, comments, docstrings, and technical terminology consistent with the existing project.
- When asked to prepare a task or prompt for another coding agent, write that prompt in English.
- Be concise and technically precise.
- Clearly distinguish mandatory fixes from optional improvements.

Do not overwhelm the owner with speculative concerns or extensive lists of hypothetical future problems.

## 5. Current Technology Stack

The project currently uses:

- Python 3.15.2
- FastAPI
- Pydantic v2
- pydantic-settings
- pytest
- httpx
- openapi-spec-validator
- PyYAML

Dependencies are pinned in the project configuration. Inspect the actual dependency files before changing versions or adding packages.

Do not introduce a new dependency when the existing stack can reasonably solve the problem.

## 6. Project Architecture

The initial API processing pipeline is:

OpenAPI Parser  
→ API Model / Normalizer  
→ Deterministic Test Generator  
→ Test Cases / Test Scenarios  
→ Test Execution Engine

The architecture should keep these responsibilities separated.

### OpenAPI Parser

Responsible for:

- Reading supported OpenAPI documents.
- Parsing JSON and YAML.
- Applying input-size and format restrictions.
- Rejecting unsupported or unsafe input.
- Performing OpenAPI specification validation.
- Producing parsed document data for subsequent processing.

### API Model

Responsible for representing the relevant OpenAPI information in a predictable, typed internal structure.

It should:

- Preserve API semantics.
- Keep local references as references rather than unnecessarily expanding or inlining them.
- Preserve information needed by subsequent processing stages.
- Distinguish missing values from explicitly empty values when the distinction has semantic meaning.
- Provide predictable serialization and deserialization behavior.
- Reject unsupported extra fields in strict typed models where appropriate.

### API Model Normalizer

Responsible for transforming the parsed API representation into a normalized structure suitable for deterministic test generation.

The normalizer must not silently discard information that can affect generated tests.

### Deterministic Test Generator

Responsible for generating repeatable test cases from normalized API information.

Equivalent input specifications should produce equivalent test scenarios, subject to explicitly defined generation rules.

### Test Execution Engine

Responsible for executing generated test scenarios and collecting structured execution results.

Keep test generation and test execution as separate responsibilities.

## 7. Important API Model Design Principles

The following principles are important and must be preserved.

### Strict models

The existing base model uses strict Pydantic configuration, including:

- Strict validation.
- Forbidden unexpected fields.
- Frozen model instances.

Do not weaken these settings globally to make individual tests pass.

### References

Local OpenAPI references must remain identifiable as references in the API Model.

Do not automatically inline or recursively expand every reference unless a specific architectural decision requires it.

Preserve reference metadata where supported.

External references are outside the currently intended local-reference processing scope.

### Missing versus empty values

Some OpenAPI properties have different meanings when omitted and when explicitly set to an empty value.

For example:

- Missing global `security` means no global security declaration.
- An explicitly empty global `security` array is a different state.
- Missing operation-level `security` means inherit global security.
- An explicitly empty operation-level `security` array disables inherited security requirements.

Do not collapse these states into one representation.

The same principle applies to other OpenAPI properties where absence and emptiness have different meanings.

### OpenAPI dialects

The parser currently targets OpenAPI 3.0.x and 3.1.x.

Do not assume that every JSON Schema feature behaves identically across these versions.

Preserve dialect-specific semantics where required.

### Components

The API Model has typed registries for major reusable component categories and an `other` registry for additional categories.

Do not discard supported component categories simply because they are not yet represented by dedicated typed models.

When changing serialization or validation of component registries, preserve the distinction between ordinary JSON-compatible objects and typed reference models.

### Security

Security requirements represent alternatives:

- Different requirement objects represent alternative authentication options.
- Multiple schemes inside one requirement object are required together.
- An empty security requirement object represents an anonymous alternative.

Security scheme definitions describe authentication mechanisms. Do not store actual credentials, access tokens, or secrets in the API Model.

## 8. Repository Structure

The known project structure includes:

```text
backend/
  app/
    api/
      routes/
        health.py
    services/
      openapi/
        __init__.py
        exceptions.py
        parser.py
        validator.py
        uri_references.py
        models.py
    config.py
    main.py

tests/
  conftest.py
  test_health.py
  test_openapi_parser.py
  test_api_model.py

docs/
  ARCHITECTURE.md
  DECISIONS.md
  PRODUCT_REQUIREMENTS.md
  PROJECT_OVERVIEW.md
  ROADMAP.md
  TECH_STACK.md
  DEVELOPMENT_SETUP.md
  API_MODEL_NORMALIZER_PROPOSAL.md
  API_MODEL_CONTRACT.md
```

This is a known structural snapshot, not a guaranteed exhaustive listing.

Always inspect the current repository before assuming that a file, module, or directory still exists or has not changed.

## 9. Development and Testing

The owner currently develops on Windows using PyCharm and a local virtual environment.

Typical test command:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Run tests from the repository root unless the project configuration specifies otherwise.

When adding tests:

- Follow existing naming conventions.
- Prefer focused tests that verify observable behavior.
- Test both successful and invalid inputs.
- Test serialization/deserialization where relevant.
- Test boundary conditions when they are important to the contract.
- Avoid duplicating existing coverage without a clear reason.

Warnings must be reported rather than silently ignored.

Do not suppress existing warnings globally just to make the test output cleaner.

## 10. Documentation

Documentation is part of the project architecture.

Before implementing changes that affect public behavior or architecture, inspect the relevant documentation.

Important documents include:

- `ARCHITECTURE.md` — system architecture.
- `DECISIONS.md` — architectural decisions.
- `PRODUCT_REQUIREMENTS.md` — product requirements.
- `API_MODEL_CONTRACT.md` — API Model contract.
- `API_MODEL_NORMALIZER_PROPOSAL.md` — proposed normalizer design.
- `ROADMAP.md` — development roadmap.

Update documentation when a task explicitly requires it or when a change makes existing documentation materially incorrect.

Do not update unrelated documentation.

Do not invent architectural decisions or claim that a proposal has been approved.

## 11. Security and Reliability

API Sentinel processes API specifications and may eventually execute requests against external API endpoints.

Keep security and reliability in mind.

In particular:

- Never log credentials, authorization headers, access tokens, or secrets.
- Do not hardcode secrets.
- Validate untrusted input.
- Avoid unsafe YAML parsing.
- Be careful with external references and network access.
- Consider SSRF risks when implementing API request execution.
- Apply sensible request timeouts and execution limits.
- Avoid unbounded recursion and uncontrolled resource consumption.
- Keep error reporting useful without exposing sensitive information.

Do not implement security-sensitive behavior based on assumptions. Raise concerns when requirements are unclear.

## 12. Current Development Status

The following is the current project status as of 2026-11-11. Always verify it against the actual repository and current Git state.

### Completed or implemented

- Initial FastAPI project structure and health endpoints.
- OpenAPI Parser: JSON/YAML parsing, safety limits, and OpenAPI 3.0.x/3.1.x validation.
- API Model: strict Pydantic v2 representations, schema preservation, references, and serialization behavior.
- API Model Normalizer: validated document to `NormalizationResult`, including components, endpoints, parameter merging, and diagnostics; implementation is covered by tests.
- Phase 3 — OpenAPI Processing: completed.
- Deterministic Test Generator (Phase 4, partially implemented): `generate_test_suite()` in `backend/app/services/test_generation/` turns a `NormalizationResult` into a `GeneratedTestSuite` of declarative test cases and `GenerationDiagnostic` values. It does not execute requests. It is covered by `tests/test_test_generation.py`. Implemented:
  - Positive baseline case per operation: required parameters (and path parameters) with direct primitive schemas, an `application/json` request body, effective security, and declared inline response expectations. Optional query, header and cookie parameters are omitted by default and no value or example is generated for them. An optional parameter whose schema is unsupported (no `schema`, `content`, a non-primitive type, an unresolved schema reference, or an unsupported keyword or composition) produces the `optional_parameter_unsupported_schema` diagnostic and does not skip the endpoint. A header parameter named `Accept`, `Content-Type` or `Authorization` (matched case-insensitively, required or optional) is ignored as the OpenAPI specification requires: no header value and no omission case is generated, its schema is not inspected, and the `reserved_header_parameter_ignored` diagnostic is reported without skipping the endpoint. A required or path parameter with an unsupported schema still skips the endpoint with its existing diagnostic, and an unresolved or cyclic Parameter Object reference still skips the endpoint, because requiredness cannot be determined.
  - Negative cases: required parameter omission, required request body omission, required object property omission, wrong JSON type, enum/const violation, string length bounds (`minLength`/`maxLength`), and numeric bounds (`minimum`/`maximum`/exclusive bounds). Negative cases do not assert an HTTP status (`request_rejection_status_unspecified`).
  - Lazy local reference resolution for `#/components/parameters/`, `#/components/requestBodies/` and `#/components/schemas/`, including alias chains. Unsupported, missing, invalid, and cyclic references produce diagnostics with the source pointer and reference URI; no case is generated from an unusable reference. Recursive required schemas are bounded by `schema_depth_limit`.
  - `$ref` siblings: only `description`, `title`, `summary` and `x-` keywords are allowed next to a schema `$ref`; any other sibling gives `unsupported_schema_reference_siblings`.
  - Numeric boundary cases for properties reached through nested local schema references.
  - Array size and uniqueness cases (`minItems`, `maxItems`, `uniqueItems`): one case per violated bound, with `minItems - 1` or `maxItems + 1` items built by deep-copying a valid item (at most 100 items). `minItems: 0`, a bound above the size limit, `uniqueItems` combined with a candidate of more than one item, and a missing or `false` `items` schema produce `array_bound_no_safe_candidate` and no case. An unsupported `items` schema produces `array_bound_unsupported_schema` and no case for a candidate that needs an item; `items` is not checked for a `minItems` violation that is an empty array, unless an `enum` or `const` candidate is validated. Other diagnostics: `array_bound_unsupported_schema` (malformed bounds and unsupported keywords), `array_bound_non_array_type`, `array_bound_contradictory_bounds`. A `uniqueItems: true` array also gets one case with a duplicate pair (`violated_constraint` is `uniqueItems`): `max(2, minItems)` independent deep copies of one valid item, appended after the `minItems` and `maxItems` cases for the same array. With `enum` or `const` on the array, the first existing literal array that satisfies the other constraints and contains a duplicate pair is used, and nothing is synthesized. `maxItems` below 2, a length above the size limit, a missing or `false` `items` schema, and no usable literal give `array_bound_no_safe_candidate` at the `uniqueItems` keyword; an unsupported `items` schema gives `array_bound_unsupported_schema`, reported once. The diagnostics of `minItems`, `maxItems` and `uniqueItems` are independent. `uniqueItems: false` or an absent keyword gives no case. The targeted arrays are a root-level request body array (`affected_property` is the empty JSON Pointer `""`, and the negative body is the array itself) and array properties of request body objects (for example `affected_property` `/tags`). Only arrays already present in the positive request body are covered, and array elements and nested arrays are not traversed.
  - Referenced responses: `#/components/responses/<name>` references (including alias chains) are resolved lazily into `ResponseExpectation`, with the operation-level reference URI kept in `response_reference_uri`. A response reference that is missing, cyclic, or outside `#/components/responses/` skips the whole endpoint with `unsupported_response_reference`; other endpoints are unaffected. Each resolved hop counts against the expansion budget. Response schemas stay as declared and are not resolved.
  - Supported `format` values (a closed set; positive generation only): `int32`, `int64` (integer, inclusive 32/64-bit ranges), `float` (number, finite, magnitude at most 3.4028234663852886e38; exact binary32 representability is not checked), `double` (number, finite), `date` (string, `YYYY-MM-DD`, real calendar date, year 0000 rejected), `date-time` (string, restricted RFC 3339 profile: full date and time, `Z` or `±HH:MM` offset, `T`/`Z` in either case, fractional seconds of one or more digits, no leap seconds, year 0000 rejected), `uuid` (string, canonical 8-4-4-4-12 hex in either case; version and variant bits are not checked) and `email` (string, restricted ASCII profile: local part of letters, digits and `._%+-` without a leading, trailing or doubled dot; at least two dot-separated domain labels of letters, digits and inner hyphens (a numeric last label such as `127.0.0.1` is accepted); bracketed IP literals, quoted parts, whitespace and international names are rejected; this is not full RFC email validation). A supported format needs its own type (a 3.1 type array uses its non-null type); a missing or different type, or a non-string `format`, gives `unsupported_schema_form` at `/format`; any other format string keeps `unsupported_schema_keyword`; `format` beside `$ref` stays unsupported. Fixed string fallbacks are used when no `const`, `enum`, example or default is valid (`2000-01-01`, `2000-01-01T00:00:00Z` then `2000-01-01T00:00:00.000Z`, `00000000-0000-4000-8000-000000000000`, `user@example.com`); invalid candidates are rejected, never replaced. Integer and float ranges act as implicit numeric constraints, so a numeric boundary case that would also leave the format range is skipped with `numeric_boundary_no_safe_candidate`. Wrong-format negative cases are not generated, and string-length or enum-violation cases that cannot be built as valid formatted strings are skipped with their existing diagnostics.
  - Security scheme validation: for every scheme named by the effective security requirements (the operation `security` if it is not `None`, otherwise the API-level `security`), the scheme is looked up in `Components.security_schemes`, `#/components/securitySchemes/<name>` alias chains are followed (one path segment, percent-decoding then `~1`/`~0`, cycle detection, one expansion budget unit per followed hop), and the concrete type must be `apiKey`, `http`, `oauth2` or `openIdConnect` (`mutualTLS` only for OpenAPI 3.1). Anonymous requirements and an explicitly empty `security` look nothing up. A missing name, or an invalid, missing or cyclic reference, gives `unsupported_security_scheme_reference` at the requirement (`/security/{index}/{name}` or `/paths/{path}/{method}/security/{index}/{name}`); an unsupported type, including `mutualTLS` in OpenAPI 3.0, gives `unsupported_security_scheme` at `/components/securitySchemes/{name}`. All failures of one endpoint are collected, in requirement order and then sorted scheme name order (identical pointer and reference URI reported once), and reported together; the whole endpoint is skipped, and no alternative or scheme is dropped. An exhausted expansion budget is not such a failure: it stops the check and gives only the existing `generation_expansion_budget_exceeded` diagnostic. Responsibility boundary: the OpenAPI Parser (`openapi-spec-validator`) rejects Security Scheme Objects with missing type-specific required fields (`apiKey` `name` and `in`, `http` `scheme`, `oauth2` `flows`, `openIdConnect` `openIdConnectUrl`) and `mutualTLS` in OpenAPI 3.0; it accepts `mutualTLS` in 3.1. This is covered by real-validator tests in `tests/test_openapi_parser.py`. The Generator relies on the validated Parser → Normalizer pipeline and intentionally does not repeat these checks; it only resolves references and validates the supported scheme type. `GeneratedTestCase.security` still holds only scheme names and scopes: scheme definitions, credentials and secrets are not copied into cases or diagnostics.
  - Deterministic generation budgets (private constants in `generator.py`, counted work only, no time or memory limits): 10,000 resolved `$ref` expansions per endpoint and 100,000 per suite; 1,000 cases per endpoint and 50,000 per suite; 100 diagnostics per endpoint and 5,000 per suite. Exhausting an expansion or case budget drops the running generation step as a whole and stops the endpoint (or the suite) with `generation_expansion_budget_exceeded` or `generation_case_budget_exceeded`. Excess ordinary diagnostics are replaced by one `generation_diagnostic_budget_exceeded` summary.
  - Failure isolation: each generation step is all-or-nothing. An unsupported schema in one negative step is reported as a diagnostic and the next negative steps for the endpoint still run. A failing positive baseline skips the endpoint.

### Current work

Phase 4 — Deterministic Test Generation Engine is the current development phase and is partially implemented (see above). The architecture proposal is in `docs/TEST_GENERATION_ENGINE_PROPOSAL.md`. Keep it separate from parsing, normalization, execution, scheduling, and AI functionality.

Planned, not yet implemented in the generator:

- Resolving `$ref`s inside response schemas, and keeping response headers and links in `ResponseExpectation`. Response schemas are retained as declared.
- Array-bound cases for optional arrays that are absent from the positive body, and for array elements and nested arrays (arrays inside other arrays). A root-level request body array is covered; only its own bounds are targeted. For a `uniqueItems` array, `minItems`/`maxItems` violations that need more than one item are not generated, because distinct valid item values are not produced. Positive generation respects `minItems` and `maxItems`. A `uniqueItems` array with `minItems` above 1 uses distinct valid item values taken only from existing candidates (item `const`/`enum` members, `example`, `examples`, `default` and the fixed fallback values, compared with JSON Schema equality, so `1` and `1.0` are duplicates and `1` and `true` are distinct); no new integer or string sequences are invented, so too few distinct candidates (for example `minItems: 3` for strings or booleans, a one-value `enum`, `const`, object or array items, `items: true`, a `uuid` without examples) give `no_positive_schema_value` and skip the endpoint.
- Schema keywords outside the supported set (for example `pattern` and any `format` other than the closed set above) and composition keywords (`allOf`, `oneOf`, `anyOf`, `not`); they produce diagnostics and no cases.
- Request media types other than `application/json`, media type `encoding`, non-primitive parameter schemas, and parameter `content`. For an optional parameter these are reported with `optional_parameter_unsupported_schema` and the parameter is omitted. Only the schema form and keywords of an omitted optional parameter are checked, so an optional parameter whose value constraints cannot be satisfied, or whose examples are invalid, gets no diagnostic.
- References outside `#/components/{parameters,requestBodies,responses,schemas,securitySchemes}/` (external references, `$defs`, anchors).
- OAuth2 scope validation against declared flows and resolved scheme metadata in generated cases. Type-specific required fields (for example `apiKey` `name` and `in`) are not planned Generator work: the OpenAPI Parser already enforces them (see the security scheme responsibility boundary above). Authentication itself belongs to the later Test Execution Engine.
- Contract validation of responses, which belongs to the later Test Execution Engine.

## 13. Git and Change Management

Before starting work:

```powershell
git status --short
git branch --show-current
git log -5 --oneline
```

Before modifying a file, inspect its current contents and relevant tests.

After implementation:

```powershell
git diff --check
git diff --stat
git diff
```

Do not reset, discard, overwrite, or revert the owner's existing uncommitted changes.

Never assume that all local changes belong to your task.

Do not commit or push unless explicitly requested.

When reporting changes, distinguish between:

- Modified working-tree files.
- Committed changes.
- Pushed changes.

## 14. General Engineering Principles

Prioritize:

1. Correctness.
2. Predictable behavior.
3. Clear separation of responsibilities.
4. Testability.
5. Maintainability.
6. Simplicity.

Avoid premature abstraction and overengineering.

The goal is a working, reliable product, not an unnecessarily complicated framework.

When choosing between a small understandable implementation and a complex generalized solution, prefer the simpler solution unless the requirements justify the complexity.

**Most important rule: implement what the product needs now, preserve the architecture needed for its next stages, and do not spend excessive time solving hypothetical future problems.**
