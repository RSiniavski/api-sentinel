# API Sentinel — Technology Stack

**Document status:** Proposed  
**Last updated:** 2026-10-30

## 1. Principles

Technology choices should prioritize:

- Reliability and maintainability.
- Compatibility with the selected Python version.
- Security.
- Developer productivity.
- Automated testing.
- Straightforward deployment.
- Extensibility without unnecessary complexity.

All proposed dependencies must be validated during implementation.

## 2. Backend

### Python

**Selected version:** Python 3.15.2 for initial development.

The version is installed locally, and the project virtual environment has been created using it.

Compatibility with the complete dependency stack must be verified before the runtime version is finalized for production and CI.

### FastAPI

**Status:** Proposed.

Purpose:

- REST API implementation.
- Request and response validation.
- API documentation.
- Application routing.
- Dependency injection.

### Pydantic

**Status:** Proposed.

Purpose:

- Data validation.
- Configuration models.
- Structured API schemas.
- Validation of internal test definitions.

Python 3.15 compatibility is available in Pydantic 2.14. Exact versions will be resolved and tested during dependency installation.

### HTTPX

**Status:** Proposed.

Purpose:

- HTTP request execution.
- Synchronous and asynchronous client support.
- Timeouts and connection configuration.
- Interaction with target APIs.

### Schemathesis

**Status:** Proposed.

Purpose:

- Property-based API testing.
- OpenAPI-driven test generation.
- Automated exploration of API behavior.
- Contract-related testing.

The platform will use a controlled integration layer rather than making the application entirely dependent on a third-party test runner.

### PyYAML

**Status:** Proposed.

Purpose:

- Parsing YAML-based OpenAPI documents.

JSON support will use Python's standard JSON capabilities where appropriate.

## 3. Database

### PostgreSQL

**Status:** Proposed.

Purpose:

- User and project data.
- API specifications and environments.
- Test definitions.
- Execution records and results.
- Future monitoring and scheduling data.

The database schema should support migrations and preserve relationships between test runs and their source specifications.

The ORM and database driver will be selected after reviewing compatibility with Python 3.15.

## 4. Frontend

### React + TypeScript

**Status:** Proposed.

Purpose:

- Application interface.
- API specification exploration.
- Test configuration.
- Execution control.
- Result visualization.
- History and reporting.

### Tailwind CSS

**Status:** Proposed.

Purpose:

- Consistent styling.
- Responsive layouts.
- Rapid interface development.

The frontend will be maintained separately from backend application code within the same repository.

## 5. Background Processing

**Status:** To be evaluated.

Background processing may be required for:

- Long-running test suites.
- Scheduled execution.
- Report generation.
- Future API monitoring.

Potential technologies include Celery with Redis.

The MVP should avoid introducing a message broker until the execution workflow genuinely requires one.

## 6. Development and Deployment

### Git and GitHub

Purpose:

- Version control.
- Source code hosting.
- Change history.
- Collaboration.
- Future CI workflows.

### PyCharm

Current IDE: PyCharm 2025.3.6.1.

### Virtual Environment

The project uses `.venv` for isolated Python dependencies.

The virtual environment must remain excluded from Git.

### Docker

**Status:** Proposed for reproducible local development and deployment.

Docker configuration will be introduced when the initial application structure and required services are established.

## 7. Testing and Quality

The project itself must include automated tests.

Proposed tools:

- pytest for application tests.
- HTTPX for API-level tests.
- Schemathesis for testing OpenAPI-driven API behavior.
- Ruff for linting and formatting, subject to Python 3.15 compatibility verification.
- A type checker, to be selected later.

The project's own test suite must be kept separate from tests executed against user-configured target APIs.

## 8. Security Considerations

Technology selection and implementation must account for:

- Secret storage and redaction.
- SSRF protection.
- Request and execution timeouts.
- Resource limits.
- Target API restrictions.
- Safe handling of uploaded specifications.
- Isolation of test execution.

## 9. Python Version Compatibility

Python 3.15.2 is the current local development interpreter.

Compatibility must be validated for the complete selected dependency set, including packages with native extensions.

If a critical dependency does not support Python 3.15 reliably, the team must evaluate alternatives or choose another supported Python version based on evidence.

The project should pin or lock dependency versions for reproducible environments.

## 10. Decisions Still Open

- Final backend dependency versions.
- Database ORM and driver.
- Database migration tool.
- Background worker and queue.
- Frontend build tooling.
- Authentication and authorization implementation.
- CI provider and deployment environment.
- Dependency locking strategy.

These decisions will be documented and updated as implementation progresses.

---

**Document status:** Proposed  
**Last updated:** 2026-10-30