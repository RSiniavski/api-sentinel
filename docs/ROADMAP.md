# API Sentinel — Development Roadmap

**Document status:** Initial proposal  
**Last updated:** 2026-11-11

## 1. Development Strategy

API Sentinel will be developed incrementally.

The primary objective is to deliver a functional and useful Basic Edition before investing in advanced AI capabilities.

Each development stage should produce a testable result.

The roadmap describes the intended direction and may evolve based on implementation experience and user feedback.

## 2. Phase 1 — Foundation

**Status:** In progress

Objectives:

- Establish the project repository.
- Prepare the development environment.
- Define product requirements.
- Document the initial architecture.
- Select the technology stack.
- Establish development conventions.

Tasks:

- [x] Create GitHub repository.
- [x] Clone repository locally.
- [x] Install Python.
- [x] Create virtual environment.
- [x] Configure PyCharm.
- [x] Create PROJECT_OVERVIEW.md.
- [x] Create PRODUCT_REQUIREMENTS.md.
- [x] Create ARCHITECTURE.md.
- [x] Create TECH_STACK.md.
- [x] Create DEVELOPMENT_SETUP.md.
- [ ] Create ROADMAP.md.
- [ ] Create DECISIONS.md.
- [ ] Establish initial repository structure.

Completion criteria:

- Project documentation is available in the repository.
- Development environment is reproducible.
- Initial architectural and technical decisions are recorded.

## 3. Phase 2 — Backend Foundation

Objectives:

- Establish the backend application.
- Implement the initial application structure.
- Configure FastAPI.
- Introduce configuration management.
- Establish logging.
- Add health-check endpoint.
- Configure initial automated tests.

Expected result:

A running API Sentinel backend with a clean modular structure and basic automated tests.

## 4. Phase 3 — OpenAPI Processing

Objectives:

- Implement specification upload.
- Support YAML and JSON.
- Validate OpenAPI documents.
- Parse API metadata.
- Discover endpoints and HTTP methods.
- Extract parameters, schemas, and authentication definitions.
- Normalize parsed specifications into an internal representation.

Expected result:

Users can upload an OpenAPI document and explore its API structure.

## 5. Phase 4 — Test Generation Engine

**Status:** In progress

Objectives:

- Define internal test representation.
- Implement positive test generation.
- Implement negative test generation.
- Implement boundary test generation.
- Implement required-field validation.
- Implement data-type validation.
- Retain declared response expectations in generated cases. Contract validation of responses belongs to Phase 5 (Test Execution Engine) and is not a Phase 4 implementation objective.

Expected result:

API Sentinel can generate structured and reproducible API test definitions from an OpenAPI specification.

Implemented so far:

- Internal test representation: strict, frozen output models (`GeneratedTestSuite`, `GeneratedTestCase`, `GenerationDiagnostic`).
- Positive test generation: one baseline case for each eligible operation whose positive inputs are supported (supported schema subset), plus one additional positive case without the body (`request-body-omitted`) for a supported optional request body; an unsupported optional body keeps the endpoint with a body-less baseline and the original diagnostic.
- Negative test generation: required parameter, request body and property omission; wrong JSON type; enum/const violation.
- Boundary test generation: string length bounds, numeric bounds (including properties reached through nested local `$ref`), array size bounds (`minItems`, `maxItems`) and `uniqueItems` duplicate cases for request body arrays (Array Bounds Generation, implemented): arrays present in the positive body, optional arrays absent from it, arrays below absent optional parent objects (created in the negative body only), arrays inside element 0 of a present array (objects and nested arrays), and arrays below the one element created in a present empty array (array-bound phase 4).
- Local reference resolution for parameters, request bodies and schemas, with diagnostics for unsupported, missing, invalid and cyclic references and the `$ref` sibling policy (annotations only).
- Deterministic resource budgets for reference expansions, generated cases and diagnostics, with stable budget diagnostics.
- Referenced responses: local `#/components/responses/` references, including alias chains, resolved into `ResponseExpectation`.
- Parser validation of Security Scheme Objects through `openapi-spec-validator`: type-specific required fields are enforced, and the supported types are accepted in OpenAPI 3.0.3 and 3.1.1 (`mutualTLS` only in 3.1). Covered by real-validator tests in `tests/test_openapi_parser.py`; the Generator does not repeat these checks.
- Generator security scheme validation: schemes named by the effective security requirements are looked up, local `#/components/securitySchemes/` alias chains are resolved, the scheme type is checked and, for `oauth2`, every requested scope must be declared in at least one flow of the resolved scheme (`unsupported_security_scope`); all failures of an endpoint are reported together and the endpoint is skipped.
- Schema `format` support for a closed set (`int32`, `int64`, `float`, `double`, `date`, `date-time`, `uuid`, `email`): type compatibility rules, deterministic fixed values, restricted validation profiles for `date-time`, `uuid` and `email`, and implicit numeric ranges in numeric boundary generation. Wrong-format negative cases are not generated; other request media types and `style`/`explode` remain unsupported.
- Failure isolation: an unsupported schema in one negative step does not stop the other negative steps of the endpoint.
- Request schema `additionalProperties` (boolean or schema object): enforced when validating example, default, enum and const candidates, which may keep undeclared properties that satisfy it; synthesized values contain declared properties only (no invented map keys). An unsupported or unresolvable `additionalProperties` schema rejects only the candidate being validated. No negative cases target additional properties, and `patternProperties`, `minProperties` and `maxProperties` remain unsupported.
- String `pattern` (bounded subset): validates existing example, default, enum, const, fallback, boundary and array item candidates only (no synthesis, no pattern-violation cases); limits of 128 characters, 128 quantifier bounds, 128-code-point candidates, 2,000 / 20,000 evaluations and 500,000 / 4,000,000 weighted work units per endpoint / suite, with `generation_pattern_budget_exceeded`. Parser acceptance does not guarantee Generator support.

Known limitations of Array Bounds Generation (the feature is implemented; these are documented limits, not missing core functionality):

- Only element index 0 of an array is traversed for array-bound generation, so a target that exists only in a later element is not covered.
- When an initially empty array is given elements for negative cases, exactly one element is inserted.
- An absent optional parent object with `const` or `enum` is not created: no suitable enum member is selected, and `array_bound_no_safe_candidate` is reported at the literal keyword.
- For an array with `uniqueItems: true`, `minItems`/`maxItems` violations that need more than one item are not generated (`array_bound_no_safe_candidate`), because the generator does not synthesize distinct valid item values.

Ancestor literal validation (Step 8.37):

- Required property, wrong type, string and numeric boundaries and enum/const check `const`/`enum` on the root and on every strict ancestor against the final negative body. These steps exclude their own target schema where violating its constraint is intentional (wrong type, enum/const). Blocked candidates are not generated and are reported as `negative_case_ancestor_literal_conflict`; they do not consume case budgets, and the diagnostics use the existing diagnostic limits.
- Array Bounds phases 1-4 check the same lineage, and (as phase 4 always did) also include the target array's own node. A literal on an array schema (also a root array) or on an `items` schema keeps `array_bound_no_safe_candidate`; object roots and object ancestors use `negative_case_ancestor_literal_conflict`. For phases 1-3 the target array's own literal is already honored when the candidate is chosen, so such a literal yields `array_bound_no_safe_candidate` at the bound keyword and the lineage check on the target node is a consistency guard.
- Only literals on the schemas visited by the existing traversal are checked.

Optional request bodies (Step 8.39.1):

- An optional request body gets an additional positive case without the body (variant `request-body-omitted`) directly after the baseline; the baseline ID and payload are unchanged. An unsupported optional body no longer skips the endpoint: the baseline has no body and keeps the original diagnostic. Required bodies and endpoints without a body are unchanged, and budget exhaustion is never swallowed.

Still planned:

- Support for further schema keywords (formats beyond the closed set, patterns beyond the bounded subset) and compositions.
- Request media types other than `application/json`.

## 6. Phase 5 — Test Execution Engine

Objectives:

- Implement HTTP request execution.
- Configure authentication handling.
- Add request and response validation.
- Implement timeouts and execution limits.
- Measure response times.
- Classify execution outcomes.
- Store detailed execution results.

Expected result:

Generated tests can be executed against configured API environments with useful diagnostics.

## 7. Phase 6 — Persistence and History

Objectives:

- Introduce PostgreSQL.
- Implement database models.
- Configure database migrations.
- Store specifications and environments.
- Store test suites and test definitions.
- Store execution runs and results.
- Implement execution history.

Expected result:

API Sentinel can persist project data and provide access to previous test executions.

## 8. Phase 7 — Frontend and User Experience

Objectives:

- Establish React and TypeScript frontend.
- Implement application navigation.
- Create API specification management interface.
- Create endpoint exploration interface.
- Implement environment configuration.
- Implement test suite management.
- Display execution results.
- Implement history and reporting views.

Expected result:

A usable web application for managing and executing API tests.

## 9. Phase 8 — Basic Edition Release

Objectives:

- Complete the MVP workflow.
- Improve usability.
- Perform integration testing.
- Validate security controls.
- Test against real-world APIs.
- Fix critical defects.
- Prepare installation and deployment documentation.
- Publish an initial release.

Completion criteria:

A user can upload an OpenAPI specification, configure an API environment, generate tests, execute them, and review results and execution history.

## 10. Phase 9 — Basic Edition Improvements

Potential capabilities:

- Scheduled test execution.
- Background processing.
- Advanced reporting.
- Test suite customization.
- Additional authentication methods.
- Improved coverage.
- API monitoring.
- Webhook-triggered test execution.

Priorities will be determined using feedback from real users.

## 11. Phase 10 — AI Edition

This phase begins after the Basic Edition is stable and validated.

Potential capabilities:

- Business documentation ingestion.
- Business-rule extraction.
- AI-assisted scenario generation.
- Multi-step API workflow generation.
- Endpoint dependency analysis.
- Intelligent failure analysis.
- Coverage recommendations.
- Test maintenance assistance.

AI-generated test definitions must be validated before execution.

## 12. Long-Term Vision

API Sentinel should evolve into a platform that combines:

- Specification-driven API testing.
- Automated test generation.
- API monitoring.
- Business-oriented testing.
- Intelligent quality analysis.

The long-term objective is to reduce repetitive QA work and help teams focus on product quality rather than manual test preparation.

---

**Document status:** Initial proposal  
**Last updated:** 2026-10-30