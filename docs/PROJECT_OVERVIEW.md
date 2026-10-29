# API Sentinel — Project Overview

**Product:** API Sentinel  
**Positioning:** Intelligent API Testing & Monitoring  
**Tagline:** Automate the routine. Focus on quality.  
**Repository:** https://github.com/RSiniavski/api-sentinel  
**Status:** Initial development / Foundation stage

---

## 1. Product Vision

API Sentinel is a platform designed to simplify API testing and monitoring, reduce repetitive manual work, and help QA engineers and development teams improve API quality.

The primary goal is to automate the creation, execution, and analysis of API tests using OpenAPI specifications as the initial source of truth.

The product should make API testing accessible, efficient, reproducible, and easy to maintain.

## 2. Product Strategy

Development will follow two major stages.

### Basic Edition

The first release should provide a useful, reliable, deterministic API testing platform.

Planned capabilities:

- Upload OpenAPI / Swagger specifications in YAML or JSON.
- Parse and validate API specifications.
- Discover endpoints, methods, parameters, schemas, authentication mechanisms, and response contracts.
- Automatically generate positive, negative, boundary, and contract tests.
- Execute generated tests against configured API environments.
- Configure authentication credentials and tokens.
- Store execution results and test history.
- Display detailed reports and reproducible failures.
- Support scheduled test execution.

### AI Edition

AI capabilities will be introduced after the Basic Edition is stable and validated by real users.

Planned capabilities:

- Analyze business requirements and API documentation.
- Accept documents such as PDF, DOCX, Markdown, and plain text.
- Understand business rules, user roles, and endpoint dependencies.
- Generate business-oriented test scenarios.
- Create multi-step API workflows.
- Analyze test failures and suggest possible causes.
- Identify gaps in business-rule coverage.
- Help maintain tests after API specification changes.

**Important architectural principle:** AI should propose structured test definitions, while a deterministic and controlled test engine executes them. Arbitrary AI-generated code must never execute directly on the server.

## 3. Target Audience

- QA Engineers
- QA Automation Engineers
- Backend Developers
- Development Teams
- Small and medium-sized software companies

The product should be especially useful for teams that want to improve API testing without building and maintaining a large custom automation framework.

## 4. Important Product Limitations

OpenAPI specifications provide a strong foundation for contract-based test generation, but they do not describe every aspect of business logic.

The platform must distinguish between:

- Contract validation.
- Input validation.
- Boundary and negative testing.
- Business logic validation.
- Stateful and multi-step workflows.

Business-oriented testing will require additional information, such as requirements, documentation, test data, or explicitly defined workflows.

## 5. Proposed Technology Stack

The following stack is a starting proposal and will be validated during implementation.

| Component | Proposed technology |
|---|---|
| Backend | Python + FastAPI |
| API test engine | Schemathesis + custom test logic |
| HTTP client | HTTPX |
| Database | PostgreSQL |
| Background jobs | To be evaluated |
| Frontend | React + TypeScript |
| UI styling | Tailwind CSS |
| Deployment | Docker |

The architecture should allow future AI integration without requiring a complete rewrite of the Basic Edition.

## 6. Security Principles

Security must be considered from the beginning.

- Protect authentication credentials and API tokens.
- Prevent SSRF and unauthorized access to internal network resources.
- Isolate test execution.
- Avoid destructive operations without explicit authorization.
- Separate API test failures from infrastructure and execution errors.
- Keep execution logs useful without exposing secrets.
- Apply resource and execution limits.

## 7. Development Environment

Current local development environment:

| Component | Current configuration |
|---|---|
| Operating system | Windows |
| IDE | PyCharm 2025.3.6.1 |
| Python | 3.15.2 |
| Virtual environment | `.venv` |
| Version control | Git |
| Remote repository | GitHub |

Local project directory:

`D:\PETS\api-sentinel`

The virtual environment is excluded from Git through `.gitignore`.

Python dependencies must be installed inside the project's isolated environment.

Compatibility of the selected Python version with all production dependencies must be verified before committing to it as the project's long-term runtime.

## 8. Development Principles

1. Build incrementally.
2. Keep the architecture modular.
3. Prefer readable and maintainable code.
4. Avoid unnecessary complexity in the MVP.
5. Document important decisions.
6. Validate functionality against real APIs.
7. Use automated tests for the platform itself.
8. Treat security as a core requirement, not a later enhancement.

The first real user and QA feedback source will be the project's creator, who will validate the platform against practical API testing scenarios.

## 9. Current Project Status

Completed:

- [x] GitHub repository created.
- [x] Repository cloned locally.
- [x] Python 3.15.2 installed.
- [x] Virtual environment `.venv` created.
- [x] Git configured and working.
- [x] `.venv` verified as ignored by Git.
- [x] PyCharm configured to use the project interpreter.

Next steps:

- [ ] Create documentation directory and initial project documents.
- [ ] Define the initial architecture.
- [ ] Finalize the MVP scope.
- [ ] Establish backend project structure.
- [ ] Implement the first application startup.
- [ ] Add initial automated tests.

---

**Document status:** Initial draft  
**Last updated:** 2026-10-30