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

The following is the last known project status as of 2026-10-31. Always verify it against the actual repository and current Git state.

### Completed or implemented

- Initial FastAPI project structure.
- OpenAPI document parsing and validation.
- JSON and YAML input handling.
- OpenAPI 3.0.x and 3.1.x support in the parser/validation stage.
- Initial API Model implementation.
- Typed representations for core API structures.
- Local reference representation.
- Initial serialization and deserialization coverage.
- Tests for major API Model invariants.

### Current work

The API Model is undergoing a final focused audit.

The `Components.other` registry received additional tests covering:

- `examples`
- `headers`
- `links`
- `callbacks`
- `pathItems`
- JSON-compatible object preservation.
- Local reference preservation.
- Serialization and reconstruction.
- Rejection of unsupported fields.

A serialization round-trip issue was identified: reference models could be reconstructed as ordinary dictionaries. A targeted validator was introduced to restore `LocalReference` instances.

The latest proposed refinement to that validator is still subject to review. Do not assume that the latest local changes have been committed or pushed.

### Next planned stage

After the API Model audit is completed, proceed to the API Model Normalizer.

Use `docs/API_MODEL_NORMALIZER_PROPOSAL.md` and the current API Model contract as the starting point.

Do not begin implementing the deterministic test generator before the normalized API representation is sufficiently defined.

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