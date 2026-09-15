# AI Coding Agent Fleet Control Plane

## 1. Objective

Build a self-hosted, Kubernetes-native control plane for managing multiple AI coding agents working across multiple software projects.

The system should function as an **AI development fleet manager** rather than as another coding agent.

The platform should allow a user to:

- Create and manage software projects.
- Connect projects to Git repositories.
- Provision isolated development workspaces for projects.
- Run one or more coding agents inside those workspaces.
- Assign tasks to specific agents.
- Monitor agent and task status.
- View agent activity and results.
- Provide project-specific credentials/secrets.
- Apply project-specific security and LLM policies.
- Centralize LLM access through an LLM gateway.
- Track LLM usage by project, agent, and task.
- Eventually allow external automation systems such as Hermes to control the platform entirely through an API.
- Eventually support multiple workspace backends such as Kubernetes, DevPod, Coder, Docker, Podman, or other providers without changing the core application.

The system should be API-first, modular, self-hosted, open-source friendly, and designed to avoid reinventing existing infrastructure.

## 2. Core Concept

The platform should be thought of as an **AI coding agent control plane**.

It sits above existing tools rather than replacing them.

The intended architecture is:

```
                         USER
                          |
              +-----------+-----------+
              |                       |
          Web/Desktop              Hermes
              |                       |
              +-----------+-----------+
                          |
                          v
                +--------------------+
                |   CONTROL PLANE    |
                |                    |
                | Projects           |
                | Environments       |
                | Agents             |
                | Tasks              |
                | Credentials        |
                | Policies           |
                | Usage              |
                | API                |
                +---------+----------+
                          |
                 Workspace Provider
                          |
                    +-----+-----+
                    |           |
                  DevPod      future
                    |        providers
                    v
                   k3s
                    |
        +-----------+-----------+
        |           |           |
     Project A   Project B   Project C
        |           |           |
     workspace   workspace   workspace
        |           |           |
      agents     agents     agents
        |           |           |
        +-----------+-----------+
                    |
                    v
              LLM Gateway
                    |
          +---------+---------+
          |         |         |
        Ollama   OpenAI   Anthropic
```

The important distinction is:

- **The control plane is the product.**
- DevPod, T3 Code, OpenCode, Claude Code, LiteLLM, Kubernetes, Git, etc. are supporting infrastructure/components.

## 3. Design Principles

### 3.1 Do not reinvent existing tools

Use existing open-source projects wherever practical.

Examples:

- DevPod for workspace provisioning.
- Kubernetes for workload isolation.
- T3 Code as an existing agent UI/control surface.
- OpenCode/Claude Code/Codex/etc. as coding agents.
- LiteLLM or another compatible gateway for LLM routing.
- Git for source control.
- Kubernetes Secrets or an external secret manager for credentials.

The custom application should primarily implement the **orchestration, state, policy, API, and fleet-management logic** that connects these components.

## 4. First Deployment Target

The first deployment target is **k3s/Kubernetes**.

The system should be self-hosted.

Do not assume a cloud-managed Kubernetes service.

The architecture should work on a small homelab cluster as well as a larger cluster.

The first workspace implementation should use:

- DevPod + Kubernetes provider.

Do not build a custom Kubernetes workspace provisioning system for the first version unless absolutely necessary.

## 5. Workspace Architecture

A project should have one or more isolated environments/workspaces.

The initial implementation should use approximately:

```
Project
   |
   +-- DevPod workspace
          |
          +-- Kubernetes Pod
          |
          +-- project filesystem
          +-- Git repository
          +-- coding agents
          +-- development tools
          +-- T3 Code server
```

Initially, prefer **one workspace per project** rather than one Kubernetes pod per agent.

Multiple agents can operate inside the same project workspace using separate Git worktrees.

Example:

```
Project: ComplianceFlow

Workspace
|
+-- Agent: backend
|     |
|     +-- worktree/backend
|
+-- Agent: frontend
|     |
|     +-- worktree/frontend
|
+-- Agent: reviewer
      |
      +-- worktree/reviewer
```

This reduces resource consumption.

The architecture must not permanently assume this model, however.

The workspace abstraction should allow future configurations such as:

```
Project
|
+-- Agent A
|     +-- Workspace A
|
+-- Agent B
      +-- Workspace B
```

if stronger isolation becomes necessary.

## 6. Workspace Provider Abstraction

Create a workspace-provider abstraction.

The control plane should not directly depend on DevPod-specific implementation details.

Conceptually:

```
WorkspaceProvider
|
+-- create()
+-- start()
+-- stop()
+-- restart()
+-- destroy()
+-- status()
+-- connect()
+-- execute()
+-- getLogs()
```

Initial implementation:

- `DevPodKubernetesProvider`

Future implementations may include:

- `CoderProvider`
- `KubernetesProvider`
- `DockerProvider`
- `PodmanProvider`
- `SSHProvider`

The rest of the system should interact with the abstract provider.

For example:

```
Control Plane
      |
      v
WorkspaceProvider.create(project)
      |
      v
DevPod Kubernetes
```

The control plane should not need to know exactly how DevPod creates the pod.

## 7. Project Model

A project represents an independently managed software project.

A project should contain metadata similar to:

```yaml
project:
  id:
  name:
  description:
  repository:
    url:
    provider:
    branch:

  workspace:
    provider:
    cpu:
    memory:
    storage:

  agents:

  credentials:

  llmPolicy:

  policies:
```

Example:

```yaml
name: complianceflow

repository:
  url: https://github.com/example/complianceflow
  branch: main

workspace:
  provider: devpod
  cpu: 4
  memory: 8Gi
  storage: 50Gi

agents:
  - backend
  - frontend
  - reviewer

credentials:
  - github
  - database
  - sentry

llmPolicy:
  localOnly: false
```

## 8. Agent Model

An agent represents an AI coding worker.

An agent should have:

- id
- name
- project
- provider
- role
- workspace
- status
- configuration
- currentTask
- worktree

Example:

```
Project: ComplianceFlow

Agents:

Backend
  Provider: OpenCode
  Role: backend development

Frontend
  Provider: Claude Code
  Role: frontend development

Reviewer
  Provider: OpenCode
  Role: code review
```

The system should not assume that every agent uses the same coding-agent implementation.

Possible providers include:

- OpenCode
- Claude Code
- Codex
- OpenHands
- future agents

The agent abstraction should therefore be **provider-neutral**.

## 9. Agent Provider Abstraction

Create an agent-provider interface.

Conceptually:

```
AgentProvider
|
+-- start()
+-- stop()
+-- executeTask()
+-- cancelTask()
+-- getStatus()
+-- getLogs()
+-- sendInput()
```

Initial implementations may wrap existing tools.

For example:

- `OpenCodeProvider`
- `ClaudeCodeProvider`
- `CodexProvider`

Do not implement an AI coding model from scratch.

The platform should orchestrate existing coding agents.

## 10. T3 Code Integration

T3 Code should initially be treated as an existing client/control surface for coding environments.

T3 Code should run **inside the project workspace** as a remote environment/server.

Conceptually:

```
T3 Code client
      |
      | HTTP/WebSocket
      v
Project Workspace
      |
      +-- T3 Code server
              |
              +-- OpenCode
              +-- Claude Code
              +-- other supported agents
```

The platform should not make T3 Code the fundamental architecture.

It should be possible to eventually replace T3 Code with another UI without rebuilding the control plane.

The control plane should own project/environment/agent identity.

T3 should be considered one way of interacting with the environment.

## 11. LLM Gateway

The platform should centralize LLM access through an LLM gateway.

The initial candidate is LiteLLM.

Conceptually:

```
Coding Agent
      |
      v
LLM Gateway
      |
      +-- Ollama
      +-- OpenAI
      +-- Anthropic
      +-- Google
      +-- other providers
```

Agents should preferably not receive raw provider API credentials.

Instead:

```
Agent
  |
  | OpenAI-compatible API
  v
LLM Gateway
  |
  +-- authentication
  +-- routing
  +-- model selection
  +-- usage tracking
  +-- rate limiting
  +-- budgets
  +-- provider credentials
```

## 12. Logical Model Names

The platform should eventually allow projects to request logical capabilities rather than hardcoded providers.

For example:

- coding
- reasoning
- fast
- cheap
- local
- premium

The LLM gateway can then decide which actual model handles the request.

Example:

```
Agent requests:

model = coding
```

Gateway policy:

```
coding
   |
   +-- preferred: local Qwen
   +-- fallback: cloud model
```

This makes model infrastructure replaceable.

## 13. LLM Usage Tracking

The platform should eventually track:

- user
- project
- environment
- agent
- task
- provider
- model
- timestamp
- input tokens
- output tokens
- cached tokens
- cost

Example:

```
Project: ComplianceFlow
Agent: Backend
Task: #1842
Model: qwen3
Input: 12,431 tokens
Output: 4,221 tokens
Cost: local/zero external cost
```

For cloud models:

```
Project: Project B
Agent: Reviewer
Task: #912
Model: Claude
Input: ...
Output: ...
Cost: $...
```

This will allow project-level accounting and budgets.

## 14. LLM Policies

Projects should eventually be able to specify policies such as:

- local-only
- cloud-allowed
- cloud-required
- allowed-models
- blocked-models
- monthly-budget
- per-task-budget

Example:

```yaml
llmPolicy:
  mode: local-only
```

or:

```yaml
llmPolicy:
  mode: cloudAllowed
  monthlyBudget: 25
```

The policy should be enforced by the gateway/control plane rather than relying on the agent to behave correctly.

## 15. Project Credentials

Projects need their own credentials.

Examples:

- GitHub
- AWS
- database
- Sentry
- Docker registry
- package registries
- cloud APIs

Credentials must be **project-scoped**.

Example:

```
Project A
|
+-- GitHub credential A
+-- Database credential A
+-- Sentry credential A

Project B
|
+-- GitHub credential B
+-- Database credential B
```

Project A must not be able to access Project B credentials.

## 16. Kubernetes Secret Architecture

For Kubernetes, prefer Kubernetes Secrets or an external secret system.

The control plane should store references/metadata, not plaintext secrets whenever practical.

Example:

```yaml
credentials:
  - name: github
    secretRef:
      name: complianceflow-github

  - name: database
    secretRef:
      name: complianceflow-database
```

The actual secret values should be injected into the workspace using Kubernetes mechanisms.

Do not expose secrets through the normal API.

The API should return metadata such as:

```
github
database
sentry
```

but never:

```
github_token: ghp_...
```

## 17. Credential Scope

Credentials should eventually support scopes such as:

- project
- environment
- agent
- task

Example:

```
github-readwrite
  allowed agents:
    - backend
    - frontend

github-readonly
  allowed agents:
    - reviewer
```

This enables more granular security later.

## 18. Kubernetes Isolation

Each project should ideally receive a Kubernetes security boundary.

A possible model:

```
Project
|
+-- Namespace
+-- ServiceAccount
+-- Role/RoleBinding
+-- Secrets
+-- PVC
+-- NetworkPolicy
+-- Workspace Pod
+-- Services
```

The exact implementation can be simplified for the first prototype.

The fundamental requirement is:

**One project's agents must not automatically have access to another project's files, secrets, or Kubernetes resources.**

## 19. Task Model

A task represents work assigned to an agent.

Example:

```json
{
  "project": "complianceflow",
  "agent": "backend",
  "prompt": "Fix the authentication bug",
  "priority": "normal"
}
```

The task should have a lifecycle.

Example:

```
queued
   |
   v
starting
   |
   v
running
   |
   +--> waiting
   |
   v
completed
```

Failure path:

```
running
   |
   v
failed
```

Cancellation:

```
running
   |
   v
cancelled
```

## 20. Task State

Track at minimum:

- id
- project
- agent
- workspace
- prompt
- status
- priority
- createdAt
- startedAt
- completedAt
- result
- error

Eventually also track:

- Git branch
- Git worktree
- commit
- pull request
- tests
- review
- LLM usage

## 21. Agent Events

The platform should have an event model.

Examples:

```
workspace.created
workspace.started
workspace.stopped
workspace.failed

agent.started
agent.progress
agent.waiting
agent.permission_required
agent.completed
agent.failed

task.created
task.started
task.completed
task.failed
task.cancelled

test.started
test.completed

commit.created
pull_request.created
```

The UI and external API should be able to consume these events.

WebSocket or Server-Sent Events are acceptable.

## 22. External API

The system must be **API-first**.

The UI should use the same API available to external automation.

Initial API concepts:

```
GET    /api/v1/projects
POST   /api/v1/projects

GET    /api/v1/projects/{project}
PATCH  /api/v1/projects/{project}
DELETE /api/v1/projects/{project}

GET    /api/v1/projects/{project}/agents
POST   /api/v1/projects/{project}/agents

GET    /api/v1/agents/{agent}
POST   /api/v1/agents/{agent}/tasks

GET    /api/v1/tasks/{task}
POST   /api/v1/tasks/{task}/cancel

GET    /api/v1/projects/{project}/usage

GET    /api/v1/projects/{project}/credentials

GET    /api/v1/events
```

Exact routes can change during implementation.

The API design should remain resource-oriented and versioned.

## 23. Hermes Integration

Hermes is an important future client.

Hermes should not need Kubernetes knowledge.

For example:

```
POST /api/v1/projects/complianceflow/tasks
```

with:

```json
{
  "agent": "backend",
  "prompt": "Fix the authentication bug",
  "priority": "normal"
}
```

The platform handles:

```
Find project
     |
Find workspace
     |
Find agent
     |
Create/locate worktree
     |
Execute task
     |
Monitor agent
     |
Track LLM usage
     |
Return result
```

Hermes should simply see:

```
queued
running
waiting
completed
failed
```

## 24. Multi-Agent Orchestration

Eventually a project may have several specialized agents.

Example:

```
ComplianceFlow
|
+-- Planner
|
+-- Backend
|
+-- Frontend
|
+-- Tester
|
+-- Reviewer
```

A high-level task could eventually be decomposed:

```
User request
      |
      v
Planner
      |
      +------> Backend
      |
      +------> Frontend
      |
      +------> Tester
                    |
                    v
                 Reviewer
```

This should be a **future** capability.

Do not build complex autonomous orchestration during the first prototype.

First prove that individual agents can reliably be created, assigned tasks, monitored, and destroyed.

## 25. Git Isolation

Multiple agents working on the same project should preferably use Git worktrees.

Example:

```
/project
|
+-- main
|
+-- worktrees/
      |
      +-- backend-task-1842
      +-- frontend-task-1843
      +-- reviewer-task-1844
```

This prevents agents from simultaneously modifying the same working tree.

The exact implementation can evolve.

## 26. Web UI

The web UI should eventually provide:

**Dashboard**

Show:

- Projects
- Active agents
- Running tasks
- Workspace health
- LLM usage
- Resource usage
- Failures

**Project page**

Show:

- Project
- Repository
- Workspace
- Agents
- Tasks
- Credentials
- LLM policy
- Usage
- Events

**Agent page**

Show:

- Agent
- Provider
- Status
- Current task
- Logs/activity
- Git worktree
- LLM usage

**Task page**

Show:

- Prompt
- Status
- Agent
- Activity
- Files changed
- Tests
- Commits
- Result

The UI is a client of the API.

## 27. T3 Code Relationship

The platform should not attempt to duplicate T3's coding UI initially.

Instead, a project can provide a way to open its T3 environment.

Conceptually:

```
Project
   |
   +-- Open T3
         |
         v
      T3 client
         |
         v
   Project environment
```

This lets us immediately benefit from T3's existing capabilities for:

- file browsing
- agent interaction
- diffs
- terminals
- source control
- agent management

while our application focuses on fleet management.

## 28. Security Requirements

Security is a primary design concern.

Requirements:

- Projects must be isolated.
- Credentials must be project-scoped.
- LLM provider credentials must not normally be exposed to agents.
- API authentication must be required.
- API authorization must eventually support project-level permissions.
- Agents should have only the Kubernetes permissions they require.
- The control plane should not expose secret values through APIs.
- Logs should avoid accidentally recording secret values.
- Network access should be controllable by project policy.
- Workspace containers should run with least privilege where practical.

Do not implement overly complicated security systems before the basic architecture works, but do not make architectural decisions that prevent proper isolation later.

## 29. Resource Management

Projects should eventually define resource requirements:

```yaml
resources:
  cpu: 4
  memory: 8Gi
  storage: 50Gi
```

Potential future settings:

- GPU
- maximum runtime
- idle timeout
- maximum concurrent agents
- maximum concurrent tasks
- priority

Eventually the scheduler should be able to decide when workspaces should start/stop.

## 30. Scheduling

Future functionality should support:

- start workspace when task arrives
- stop workspace when idle
- scheduled tasks
- nightly jobs
- periodic maintenance
- priority queues
- resource-aware scheduling

Example:

```
02:00
  |
  v
Start workspace
  |
Run dependency update agent
  |
Run tests
  |
Reviewer
  |
Stop workspace
```

Do not implement this initially.

## 31. Observability

Eventually track:

- workspace health
- agent status
- task status
- CPU
- memory
- storage
- LLM usage
- errors
- events

Prefer existing Kubernetes observability mechanisms rather than creating a monitoring system.

## 32. Persistence

The control plane itself needs persistent state.

Potential entities:

- projects
- workspaces
- agents
- tasks
- credentials metadata
- LLM policies
- usage records
- events

Use a relational database.

PostgreSQL is preferred for production.

For an initial development prototype, SQLite may be acceptable if the chosen framework supports a clean migration to PostgreSQL.

Do not create a custom database layer unnecessarily.

## 33. Event Architecture

The system should eventually be event-driven internally.

A task might produce:

```
task.created
agent.started
agent.tool_called
agent.progress
agent.waiting
agent.completed
```

The system can then:

```
event
 |
 +--> UI
 +--> API/WebSocket
 +--> usage tracking
 +--> audit log
 +--> Hermes
```

Avoid tightly coupling every subsystem directly together.

## 34. Desired-State Philosophy

Where practical, use a desired-state model.

For example:

**Desired:**

```
Project A
  workspace = running
  agents = backend, frontend
```

The system reconciles actual state:

**Actual:**

```
Project A
  workspace = stopped
  backend = missing
  frontend = running
```

and works toward:

```
Project A
  workspace = running
  backend = running
  frontend = running
```

This is especially appropriate for Kubernetes.

## 35. Kubernetes Operator

Eventually, the control plane should have a Kubernetes controller/operator component.

Potential custom resources:

- Project
- Workspace
- Agent
- Task

Example:

```yaml
apiVersion: agents.example.com/v1
kind: Project

metadata:
  name: complianceflow

spec:
  repository:
    url: ...

  workspace:
    provider: devpod
```

The operator/controller can reconcile these resources.

However:

Do not implement a complete Kubernetes operator before proving the DevPod architecture.

The first prototype can use a normal service/API that invokes DevPod.

Once the lifecycle is understood, decide which responsibilities belong in CRDs/controllers.

## 36. Initial Prototype Scope

The first milestone should be deliberately small.

**Milestone 1**

Prove:

```
DevPod
   ↓
Kubernetes/k3s
   ↓
project workspace
   ↓
T3 Code
   ↓
OpenCode
   ↓
Git repository
```

The agent must be able to:

- Start in the workspace.
- See the repository.
- Modify files.
- Run commands.
- Run tests.
- Create a Git diff.
- Commit changes.

## 37. Milestone 2

Add:

```
LiteLLM
   ↓
Ollama
```

Prove that an agent can use the internal LLM gateway rather than directly accessing the model provider.

## 38. Milestone 3

Create two projects:

```
Project A
   |
   +-- Workspace A

Project B
   |
   +-- Workspace B
```

Verify:

- A cannot read B's files.
- A cannot access B's secrets.
- A cannot access B's Git credentials.
- B cannot access A's resources.
- Both can access the LLM gateway.
- LLM usage can be attributed to the correct project.

This is an important architecture validation milestone.

## 39. Milestone 4

Add multiple agents to one project:

```
Project
|
+-- Backend
+-- Frontend
+-- Reviewer
```

Use separate Git worktrees.

Verify that agents can operate concurrently without corrupting each other's work.

## 40. Milestone 5

Build the first version of the custom control plane.

Minimum functionality:

- Create project
- Delete project
- Start workspace
- Stop workspace
- View workspace status
- Create agent
- Start agent
- Stop agent
- Create task
- View task
- Cancel task
- View events

Do not attempt to build the complete dashboard yet.

## 41. Milestone 6

Add:

- Project credentials
- LLM policies
- Usage tracking
- API authentication

## 42. Milestone 7

Add Hermes integration.

Hermes should be able to:

- create task
- query task
- cancel task
- query agent
- query project
- subscribe to events

without knowing Kubernetes or DevPod details.

## 43. What We Should NOT Build

Do not build:

- Our own coding agent.
- Our own LLM inference server.
- Our own Git implementation.
- Our own container runtime.
- Our own Kubernetes scheduler.
- Our own secret-management system.
- Our own IDE initially.
- A replacement for T3 Code initially.
- A replacement for DevPod initially.
- A replacement for LiteLLM initially.

The product should be the orchestration/control layer.

## 44. Technology Selection

Technology choices should favor:

- open source
- self-hosting
- Linux/Kubernetes
- APIs
- modularity
- simple deployment
- low resource usage
- PostgreSQL compatibility
- containerization
- strong TypeScript/Python ecosystem

The coding agent should research current stable versions before selecting dependencies.

Avoid introducing dependencies merely because they are fashionable.

## 45. Repository Structure

A possible repository structure:

```
ai-agent-control-plane/
|
+-- apps/
|   +-- api/
|   +-- web/
|
+-- packages/
|   +-- core/
|   +-- workspace/
|   +-- agents/
|   +-- llm/
|   +-- credentials/
|   +-- events/
|
+-- providers/
|   +-- devpod/
|   +-- agents/
|
+-- deploy/
|   +-- kubernetes/
|   +-- helm/
|
+-- docs/
|
+-- tests/
|
+-- README.md
```

This is only a starting suggestion.

Do not force this exact structure if another structure is demonstrably better.

## 46. Abstraction Boundaries

The following boundaries are especially important:

```
Control Plane
      |
      +-- Workspace Provider
      |
      +-- Agent Provider
      |
      +-- LLM Gateway
      |
      +-- Credential Provider
      |
      +-- Source Control Provider
```

For example:

```
WorkspaceProvider
    |
    +-- DevPod

AgentProvider
    |
    +-- OpenCode
    +-- Claude Code
    +-- Codex

CredentialProvider
    |
    +-- Kubernetes Secrets
    +-- future Vault
    +-- future External Secrets

SourceControlProvider
    |
    +-- GitHub
    +-- GitLab
    +-- Gitea
```

The interfaces should be small and capability-based.

Do not create huge abstractions for hypothetical functionality.

## 47. Failure Handling

The platform must expect things to fail.

Examples:

- workspace creation failed
- agent crashed
- LLM unavailable
- Git authentication failed
- repository unavailable
- pod deleted
- PVC unavailable
- task timeout
- network unavailable

Failures should result in explicit state.

Example:

```
workspace.status = failed
workspace.error = ...
```

Do not silently retry indefinitely.

Retries should have limits and be observable.

## 48. Auditability

Eventually the platform should record important actions:

- who created project
- who created credential
- who started agent
- who submitted task
- who cancelled task
- which model was used
- which workspace was accessed

This is especially important if the platform eventually becomes a multi-user product.

## 49. Future Product Direction

The eventual platform should be capable of managing an entire AI-assisted software-development fleet.

Example:

```
                    AI DEVELOPMENT FLEET

Projects
|
+-- SaaS A
|    |
|    +-- Backend Agent
|    +-- Frontend Agent
|    +-- Test Agent
|    +-- Reviewer
|
+-- SaaS B
|    |
|    +-- Backend Agent
|    +-- Security Agent
|
+-- Internal Tool
     |
     +-- General Agent
     +-- Documentation Agent
```

The user should be able to see the entire fleet from one place.

Tasks can be dispatched manually or programmatically.

Resources can be managed centrally.

LLM usage can be tracked centrally.

Security policies can be enforced centrally.

## 50. Long-Term Vision

The long-term goal is not simply:

> "A web UI for OpenCode."

It is:

> A control plane for autonomous software-development agents.

The system should eventually manage:

```
Projects
    ↓
Environments
    ↓
Agents
    ↓
Tasks
    ↓
Code
    ↓
Tests
    ↓
Reviews
    ↓
Pull Requests
    ↓
Deployment
```

while maintaining:

- Isolation
- Security
- Credentials
- LLM policies
- Resource limits
- Usage accounting
- Auditability
- Automation

The platform should be capable of running on a personal k3s cluster but architected well enough that it could eventually operate a much larger fleet.

## 51. Most Important Architectural Rule

Do not tightly couple the product to DevPod, T3 Code, OpenCode, or LiteLLM.

They are implementation components.

The platform's durable concepts are:

- Project
- Workspace
- Agent
- Task
- Credential
- Policy
- Model
- Usage
- Event

Those concepts should remain stable even if the underlying tools change.

For example:

**Today:**

```
Project
  ↓
DevPod
  ↓
Kubernetes
  ↓
OpenCode
  ↓
LiteLLM
```

**Tomorrow:**

```
Project
  ↓
Coder
  ↓
Kubernetes
  ↓
Claude Code
  ↓
different LLM gateway
```

The application should still understand both as:

```
Project
  └── Workspace
        └── Agent
              └── Task
```

## 52. Immediate Development Objective

Do not attempt to implement the entire specification immediately.

The first coding objective is:

**Build a working proof-of-concept that provisions a project workspace through DevPod on k3s, runs T3 Code and an existing coding agent inside that workspace, connects the agent to an LLM gateway, and proves that the architecture can support multiple isolated projects.**

Before implementing the full control plane, document and validate:

- How DevPod creates the Kubernetes workspace.
- How the workspace persists data.
- How T3 Code runs as a remote environment.
- How T3 connects to the coding agent.
- How OpenCode/other agents are installed.
- How Git authentication works.
- How project credentials are injected.
- How the LLM gateway is reached.
- How two project workspaces can be isolated.
- Which pieces need custom code versus existing tooling.

Only after those questions are experimentally validated should the full control-plane implementation begin.

## 53. Coding-Agent Instructions

You are working on the implementation of this architecture.

Before writing substantial code:

1. Inspect the repository.
2. Determine whether an existing application/framework is already present.
3. Research the current versions/documentation of DevPod, T3 Code, OpenCode, Kubernetes, and the chosen LLM gateway.
4. Identify current APIs and limitations rather than relying on assumptions.
5. Produce a short implementation plan.
6. Clearly distinguish:
   - confirmed capabilities
   - assumptions
   - things that need to be experimentally verified.
7. Prefer existing APIs and libraries over custom implementations.
8. Keep provider-specific code behind interfaces.
9. Do not implement features outside the current milestone without justification.
10. Keep the system runnable locally for development where practical.

For the first milestone, prioritize **proving the architecture** over building a polished UI.

The desired result of the first milestone is a reproducible development environment where:

```
Developer machine
      |
      v
DevPod
      |
      v
k3s
      |
      v
isolated project workspace
      |
      +-- T3 Code
      +-- OpenCode
      +-- Git
      +-- project dependencies
      |
      v
LLM gateway
      |
      v
Ollama / other LLM
```

works reliably.

After that proof succeeds, build the control-plane API around the validated components.
