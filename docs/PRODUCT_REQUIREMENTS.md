# API Sentinel — Product Requirements

**Document status:** Initial draft  
**Last updated:** 2026-10-30

## 1. Purpose

This document defines the functional and non-functional requirements of API Sentinel.

It describes what the product must do, how its main components should behave, and which capabilities belong to each development stage.

## 2. Product Scope

API Sentinel is an API testing and monitoring platform that uses OpenAPI specifications to discover API functionality and automatically generate executable tests.

The product will initially focus on deterministic API testing. AI-powered capabilities will be introduced in a later edition.

## 3. MVP — Minimum Viable Product

The MVP must provide a complete basic workflow:

1. Upload an OpenAPI specification.
2. Parse and validate the specification.
3. Display discovered API endpoints.
4. Configure the target API environment.
5. Configure authentication.
6. Generate tests automatically.
7. Execute generated tests.
8. Display execution results.
9. Save execution history.

The MVP should demonstrate that a user can go from an OpenAPI document to meaningful automated API test results without manually writing test code.

## 4. Functional Requirements

### FR-001 — OpenAPI Specification Upload

The system must allow users to upload OpenAPI specifications in YAML and JSON formats.

### FR-002 — Specification Validation

The system must validate uploaded specifications and provide understandable validation errors.

### FR-003 — API Discovery

The system must extract and display:

- API endpoints.
- HTTP methods.
- Parameters.
- Request bodies.
- Authentication requirements.
- Response definitions.
- Data schemas.

### FR-004 — Environment Configuration

Users must be able to configure API base URLs and separate testing environments.

### FR-005 — Authentication Configuration

The system must support authentication mechanisms described in the OpenAPI specification.

Initial support should prioritize:

- API Key.
- Bearer Token.
- Basic Authentication.

Additional authentication methods may be introduced later.

### FR-006 — Test Generation

The system must automatically generate tests based on available API specification data.

Initial test categories:

- Positive tests.
- Negative tests.
- Boundary tests.
- Required-field validation.
- Data-type validation.
- Contract validation.

### FR-007 — Test Execution

Users must be able to execute generated tests against a configured API environment.

### FR-008 — Test Results

The system must display:

- Test name.
- Endpoint and HTTP method.
- Execution status.
- Expected result.
- Actual result.
- Response time.
- Failure details.

### FR-009 — Execution History

The system must store test execution results and allow users to review previous runs.

### FR-010 — Reproducibility

Failed tests must contain enough information to reproduce the failure, without exposing sensitive credentials.

## 5. Basic Edition — Planned Capabilities

The Basic Edition will extend the MVP with:

- More advanced test generation.
- Custom test configuration.
- Test suite management.
- Scheduled test execution.
- Background execution.
- Detailed reports.
- Execution history and comparisons.
- Improved schema and contract coverage.
- Multiple API environments.
- Exportable test reports.

## 6. AI Edition — Future Capabilities

The AI Edition is planned as a separate development stage.

Potential capabilities:

- Business requirements analysis.
- Documentation ingestion.
- Business-rule extraction.
- Business-oriented test generation.
- Multi-step API workflow generation.
- Endpoint dependency analysis.
- AI-assisted failure analysis.
- Test coverage recommendations.
- Test maintenance suggestions after specification changes.

AI-generated test definitions must be reviewed and validated before execution.

## 7. Non-Functional Requirements

### NFR-001 — Security

Sensitive credentials and tokens must be protected.

### NFR-002 — Reliability

Test execution must produce consistent and reproducible results.

### NFR-003 — Modularity

The system must use modular components to support future extensions.

### NFR-004 — Performance

The platform must support asynchronous execution of longer test suites.

### NFR-005 — Maintainability

The codebase must remain readable, testable, and maintainable.

### NFR-006 — Scalability

The architecture should allow multiple users and concurrent test executions in future releases.

### NFR-007 — Observability

The system must provide useful application logs and execution diagnostics.

## 8. Out of Scope for Initial MVP

- AI-generated business scenarios.
- Arbitrary execution of generated code.
- Complex distributed test orchestration.
- Advanced team collaboration.
- Enterprise integrations.

These capabilities may be reconsidered in later development stages.

## 9. Acceptance Criteria

The MVP will be considered functional when a user can:

1. Upload a valid OpenAPI specification.
2. See the discovered endpoints.
3. Configure an API target and authentication.
4. Generate tests automatically.
5. Execute those tests.
6. Review detailed results.
7. Reopen previous execution history.

