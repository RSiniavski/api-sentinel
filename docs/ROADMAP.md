# API Sentinel — Development Roadmap

**Document status:** Initial proposal  
**Last updated:** 2026-11-08

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
- Introduce contract validation.

Expected result:

API Sentinel can generate structured and reproducible API test definitions from an OpenAPI specification.

Implemented so far:

- Internal test representation: strict, frozen output models (`GeneratedTestSuite`, `GeneratedTestCase`, `GenerationDiagnostic`).
- Positive test generation: one baseline case per operation for the supported schema subset.
- Negative test generation: required parameter, request body and property omission; wrong JSON type; enum/const violation.
- Boundary test generation: string length bounds, numeric bounds (including properties reached through nested local `$ref`), array size bounds (`minItems`, `maxItems`) and `uniqueItems` duplicate cases for arrays present in the positive request body.
- Local reference resolution for parameters, request bodies and schemas, with diagnostics for unsupported, missing, invalid and cyclic references and the `$ref` sibling policy (annotations only).
- Deterministic resource budgets for reference expansions, generated cases and diagnostics, with stable budget diagnostics.
- Referenced responses: local `#/components/responses/` references, including alias chains, resolved into `ResponseExpectation`.
- Failure isolation: an unsupported schema in one negative step does not stop the other negative steps of the endpoint.

Still planned:

- Array bound cases for optional arrays, array elements and nested arrays.
- Support for further schema keywords (for example `pattern`, `format`) and compositions.
- Request media types other than `application/json`.
- Contract validation of responses, together with the Test Execution Engine (Phase 5).

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