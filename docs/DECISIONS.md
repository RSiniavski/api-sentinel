# API Sentinel — Architecture Decision Log

**Document status:** Active  
**Last updated:** 2026-10-30

This document records important product, architectural, and technical decisions made during API Sentinel development.

Decisions may be superseded when new information or implementation experience justifies a change.

---

## ADR-001 — Standalone Product

**Status:** Accepted  
**Date:** 2026-10-30

### Context

API Sentinel is intended to become an independent API testing and monitoring platform.

### Decision

Develop API Sentinel as a standalone product with its own architecture, repository, and development lifecycle.

### Consequences

- Independent product evolution.
- Independent deployment.
- No mandatory dependency on other applications.
- Freedom to select technologies specifically for API Sentinel.

---

## ADR-002 — Basic Edition Before AI Edition

**Status:** Accepted  
**Date:** 2026-10-30

### Context

AI-powered functionality can introduce additional complexity, infrastructure costs, and unpredictable behavior.

### Decision

Build and validate the deterministic Basic Edition before implementing AI capabilities.

### Consequences

- Earlier delivery of a useful product.
- Real-world user feedback before AI investment.
- Clear separation between deterministic testing and AI-assisted analysis.

---

## ADR-003 — OpenAPI as the Initial Source of Truth

**Status:** Accepted  
**Date:** 2026-10-30

### Context

OpenAPI specifications describe API endpoints, methods, parameters, schemas, authentication, and response contracts.

### Decision

Use OpenAPI specifications as the primary input for initial test discovery and generation.

### Consequences

- Automated endpoint discovery.
- Structured test generation.
- Contract-based validation.
- A foundation for future business-oriented testing.

OpenAPI alone does not guarantee complete business-logic coverage.

---

## ADR-004 — Structured Test Definitions

**Status:** Accepted  
**Date:** 2026-10-30

### Context

AI-generated or dynamically generated executable code introduces security and reliability risks.

### Decision

Represent generated tests as structured definitions interpreted by a controlled execution engine.

Do not execute arbitrary generated code directly.

### Consequences

- More predictable execution.
- Easier validation.
- Better security boundaries.
- Future AI integration without giving AI unrestricted execution capabilities.

---

## ADR-005 — Modular Architecture

**Status:** Accepted  
**Date:** 2026-10-30

### Context

The product is expected to evolve from basic OpenAPI testing into a broader quality platform.

### Decision

Separate parsing, test generation, test execution, persistence, frontend, and future AI responsibilities into distinct modules.

### Consequences

- Easier maintenance.
- Independent component evolution.
- Reduced coupling.
- Better extensibility.

---

## ADR-006 — Python and FastAPI

**Status:** Proposed

### Context

The backend requires a maintainable framework with support for asynchronous operations and API development.

### Decision

Use Python with FastAPI as the proposed backend technology.

### Consequences

- Python ecosystem integration.
- Automatic API documentation.
- Structured request validation.
- Suitable foundation for modular backend development.

Final dependency compatibility must be validated before production use.

---

## ADR-007 — Python 3.15.2 for Initial Development

**Status:** Accepted for local development; production suitability pending validation  
**Date:** 2026-10-30

### Context

Python 3.15.2 is installed on the primary development machine.

### Decision

Use Python 3.15.2 in the initial local development environment.

### Consequences

- New language runtime features are available.
- All dependencies must be checked for compatibility.
- Production runtime selection remains subject to integration testing.

---

## ADR-008 — Isolated Python Environment

**Status:** Accepted  
**Date:** 2026-10-30

### Context

Global Python packages can cause conflicts between projects.

### Decision

Use a project-specific virtual environment named `.venv`.

### Consequences

- Isolated dependencies.
- Reproducible project setup.
- No global dependency pollution.
- `.venv` must remain excluded from Git.

---

## ADR-009 — PostgreSQL as Proposed Database

**Status:** Proposed

### Context

The platform will need persistent storage for users, projects, specifications, test definitions, execution records, and results.

### Decision

Use PostgreSQL as the proposed primary database.

### Consequences

- Relational data model.
- Transaction support.
- Suitable foundation for future multi-user functionality.

The ORM, driver, and migration strategy remain open.

---

## ADR-010 — GitHub Repository

**Status:** Accepted  
**Date:** 2026-10-30

### Context

The project needs source control and a central repository.

### Decision

Host the project in a GitHub repository.

Repository:

https://github.com/RSiniavski/api-sentinel

### Consequences

- Version history.
- Remote backup.
- Future collaboration.
- Potential CI integration.

---

## ADR-011 — Documentation-Driven Development

**Status:** Accepted  
**Date:** 2026-10-30

### Context

The project includes multiple architectural components and a multi-stage development plan.

### Decision

Maintain product, architecture, technical, and development documentation inside the repository.

Update documentation when significant requirements or implementation decisions change.

### Consequences

- Shared source of truth.
- Better continuity.
- Easier onboarding.
- Traceability of technical decisions.

---

## ADR-012 — Dependency Compatibility Before Runtime Commitment

**Status:** Accepted  
**Date:** 2026-10-30

### Context

The newest Python version is not necessarily supported by every third-party package.

### Decision

Validate the complete dependency stack before committing to a production Python runtime.

### Consequences

- Reduced compatibility risk.
- Evidence-based runtime selection.
- Freedom to choose a different runtime if a critical dependency requires it.

---

**Maintenance rule:** New significant architectural or technical decisions should be added as new ADR entries. Existing accepted decisions should not be silently rewritten; superseded decisions must retain their history.