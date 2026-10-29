# API Sentinel — System Architecture

**Document status:** Initial proposal  
**Last updated:** 2026-10-30

## 1. Architectural Vision

API Sentinel will use a modular architecture with clearly separated responsibilities.

The system should support independent development and evolution of its main components while maintaining a consistent interface between them.

The initial architecture will prioritize simplicity, maintainability, security, and future extensibility.

## 2. High-Level Architecture

The proposed system consists of the following components:

1. Frontend — user interface.
2. Backend API — application logic and communication layer.
3. Specification Parser — OpenAPI specification processing.
4. Test Generator — automatic test generation.
5. Test Execution Engine — controlled test execution.
6. Database — persistent application and test data storage.
7. Background Worker — asynchronous and scheduled operations.
8. AI Engine — future component for intelligent analysis and scenario generation.

## 3. Component Responsibilities

### 3.1 Frontend

Responsible for user interaction.

Main responsibilities:

- User authentication and account management.
- API specification upload.
- API endpoint exploration.
- Environment configuration.
- Authentication configuration.
- Test suite management.
- Test execution control.
- Results visualization.
- Execution history.

Proposed technology: React + TypeScript.

### 3.2 Backend API

The central application layer.

Main responsibilities:

- User and project management.
- API specification management.
- Environment configuration.
- Authentication settings.
- Test suite management.
- Execution orchestration.
- Results management.
- Communication with background workers.

Proposed technology: Python + FastAPI.

### 3.3 Specification Parser

Responsible for reading and interpreting OpenAPI documents.

Main responsibilities:

- YAML and JSON parsing.
- OpenAPI version detection.
- Specification validation.
- Endpoint discovery.
- Parameter extraction.
- Request and response schema extraction.
- Authentication definition extraction.
- Reference resolution.

The parser should produce a normalized internal representation that can be consumed by other system components.

### 3.4 Test Generator

Responsible for generating test definitions from the normalized API specification.

Initial test categories:

- Positive tests.
- Negative tests.
- Boundary tests.
- Required-field validation.
- Data-type validation.
- Contract validation.

The generator should produce structured test definitions rather than arbitrary executable code.

### 3.5 Test Execution Engine

Responsible for executing generated tests.

Main responsibilities:

- HTTP request preparation.
- Authentication handling.
- Request execution.
- Response validation.
- Response time measurement.
- Result classification.
- Failure diagnostics.

The execution engine must operate with appropriate security and resource restrictions.

### 3.6 Database

Responsible for persistent storage.

Initial data categories:

- Users.
- Projects.
- API specifications.
- API environments.
- Authentication configurations.
- Test suites.
- Test definitions.
- Execution runs.
- Individual test results.

Proposed technology: PostgreSQL.

### 3.7 Background Worker

Responsible for operations that should not block normal API requests.

Potential responsibilities:

- Long-running test execution.
- Scheduled test runs.
- Report generation.
- Future monitoring tasks.

The background processing technology will be selected after evaluating actual MVP requirements.

### 3.8 AI Engine — Future Component

The AI Engine will be introduced in a later development stage.

Its responsibilities may include:

- Business requirements analysis.
- Business-rule extraction.
- Business-oriented test generation.
- Multi-step workflow suggestions.
- Failure analysis.
- Test coverage recommendations.

AI-generated definitions must pass validation before reaching the execution engine.

The AI Engine must not execute arbitrary generated code.

## 4. Main Data Flow

Initial API testing workflow:

1. User uploads an OpenAPI specification.
2. Backend receives and stores the specification.
3. Specification Parser validates and processes it.
4. Parser produces a normalized API representation.
5. Test Generator creates structured test definitions.
6. User reviews the generated tests.
7. User starts a test execution.
8. Backend schedules or initiates execution.
9. Test Execution Engine sends requests to the target API.
10. Responses are validated.
11. Execution results are stored in the database.
12. Frontend displays the results.

## 5. Architectural Boundaries

The following responsibilities must remain separated:

- Parsing must not execute API requests.
- Test generation must not directly execute tests.
- Execution must not depend on frontend implementation.
- AI must not bypass validation and execution controls.
- Authentication secrets must not be exposed in logs or reports.

## 6. Security Architecture

Security requirements apply across all components.

Initial considerations:

- Secure credential storage.
- Protection against SSRF.
- Restrictions on target API addresses.
- Execution timeouts and resource limits.
- Isolation of test execution.
- Input validation.
- Authorization checks.
- Secret redaction in logs.
- Protection against accidental destructive operations.

## 7. Extensibility

The architecture should support future additions without requiring major changes to the core application.

Potential extensions:

- AI-powered test generation.
- API monitoring.
- Webhook-triggered execution.
- Additional authentication methods.
- Custom validation plugins.
- Additional test generation strategies.
- Team collaboration.
- External integrations.

## 8. Architecture Evolution

This document describes the initial proposed architecture.

Implementation decisions may change as the product evolves.

Significant architectural changes must be recorded in `DECISIONS.md`.

---

**Document status:** Initial proposal  
**Last updated:** 2026-10-30