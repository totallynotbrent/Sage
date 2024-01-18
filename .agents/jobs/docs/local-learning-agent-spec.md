# Local Learning Agent — Specification

**Status:** Drafted from interview and transcript review  
**Scope:** Product and implementation specification only; no application code is included in this change.

## 1. Summary

Build a local-first web application that acts as a personalized AI tutor. The user can attach study materials—notes, textbooks, professor slides, and related documents—and then interact with the agent in a simple study workspace.

The tutor should combine:

- A normal, flexible chat interface.
- Document-grounded answers and teaching.
- An adaptive probe of the learner's current understanding.
- A collaborative, dependency-aware learning plan.
- Slow, incremental explanations rather than a single rushed response.
- Periodic multiple-choice checks for understanding.
- Inspectable citations that reveal the exact source excerpt used.
- Persistent local files, sessions, quiz results, and learner mastery data.

The model backend is a locally configured OpenAI-compatible endpoint:

```env
BROT_BASE_URL=http://127.0.0.1:8877/v1
BROT_API_KEY=<local BROT API key>
BROT_MODEL=deepseek/deepseek-v4-pro
```

The application should not hard-code the API key or require a cloud account of its own.

**Target platform (decided):** a single Raspberry Pi (64-bit ARM64/aarch64, 4–8 GB RAM class) running Raspberry Pi OS. The backend is **Python 3.11** built with **FastAPI** and served by **uvicorn**; the same process also serves the frontend. The app binds to `0.0.0.0` so it is reachable from a laptop or phone on the local network rather than only from the Pi itself. The BROT model endpoint runs on the same Pi by default, so `BROT_BASE_URL=http://127.0.0.1:8877/v1` remains the default and stays overridable.

## 2. Product inspiration and intended learning philosophy

The supplied transcript describes an AI teacher that reduces the inefficiency of many learners sharing a one-size-fits-many teaching outlet. The intended tutor should instead adapt its teaching path and explanations to the learner's current understanding.

Important principles extracted from the transcript:

1. **Meet the learner at the edge of understanding.** Avoid explaining material already mastered and avoid jumping over prerequisites the learner cannot yet understand.
2. **Use one consistent teaching interface over many sources.** Uploaded notes, textbooks, and slides provide perspectives and evidence, while the tutor presents them through one stable interaction.
3. **Move logistical work into the system.** The learner should spend effort on the material, not on organizing resources, deciding what to learn next, or repeatedly verifying basic facts.
4. **Probe before planning.** The tutor should measure understanding before choosing a detailed route through the material.
5. **Plan explicitly.** The learning path should be visible and dependency-aware rather than being improvised invisibly in every response.
6. **Teach one reasoning step at a time.** The agent should leave room for questions and should not rush through an entire dependency graph.
7. **Use feedback continuously.** Quizzing is useful both for the learner's own calibration and for recalibrating the tutor's model of the learner.
8. **Trust must be engineered.** The system should be conservative about uncertainty, ground claims in sources where possible, and expose evidence rather than presenting unsupported confidence.

The transcript is inspiration for behavior, not a requirement to reproduce the original Pi Agent, Obsidian, extensions, or sub-agent implementation.

## 3. Repository and research context

### Repository findings

The repository contained no files or established conventions at the time of specification. There is therefore no existing framework, package manager, persistence layer, component library, or API client to preserve. The framework decision in §17.1 is resolved: a Python 3.11 FastAPI backend served by uvicorn, with the frontend served as static files from the same process. All dependencies must install on ARM64/aarch64 Linux under Python 3.11. The app must provide a one-command local development workflow that runs on the Pi and is reachable on the LAN.

### API and file-handling findings

- The user has specified an OpenAI-compatible BROT endpoint and model.
- OpenAI-compatible chat-completions interfaces generally accept conversational messages, but generic file upload and retrieval behavior cannot be assumed from completions compatibility alone.
- The application should therefore own local file ingestion, text extraction, chunking, source metadata, and context selection. It should pass selected excerpts as message context to the configured endpoint rather than depending on provider-specific file-search APIs.
- The exact BROT route, streaming behavior, supported request fields, context limit, and error format must be verified during implementation against the running local endpoint.
- Research references consulted:
  - [OpenAI Chat Completions overview](https://developers.openai.com/api/reference/chat-completions/overview)
  - [OpenAI file inputs guide](https://developers.openai.com/api/docs/guides/file-inputs)
  - [DeepSeek API documentation](https://api-docs.deepseek.com/)
  - [BROT-compatible proxy search result](https://github.com/notBlubbll/free-buff-lol)

## 4. Goals

### Primary goals

- Provide a simple local web UI for adding study files and learning from them.
- Support a flexible conversation that can transition between ordinary questions, probing, planning, teaching, and practice.
- Make the agent's learning plan visible and collaborative.
- Give the user an adaptive but lightweight first version of the transcript's probe/plan/teach/check loop.
- Preserve study data locally so sessions and learner progress can be revisited.
- Make source use transparent through inline references and inspectable excerpts.
- Minimize unsupported or overconfident claims.
- Stream responses and provide explicit controls for study interactions.

### Secondary goals

- Build a detailed topic-level mastery map from quiz performance and session outcomes.
- Make the design extensible to later verification agents, richer retrieval, visualization generation, and additional question types.

## 5. Non-goals for v1

- Reproducing the original Pi Agent harness or Obsidian integration.
- Building a general-purpose cloud-hosted multi-user product.
- Requiring external search, browsing, or an external research provider.
- Automatic web research or mandatory verification sub-agents.
- Full semantic/vector search infrastructure. V1 uses simple local chunking and relevant-excerpt selection.
- Open-ended written-answer grading as a required quiz mode. V1 quizzes are multiple-choice only.
- Perfect OCR or guaranteed semantic extraction from every file format.
- Automatic conversion of every textbook into a complete mastery curriculum without user goals.
- Sending an entire local library to the model by default.

## 6. Target user and core use cases

The initial user is a student who has local notes, textbooks, professor slides, and possibly other study materials. They want an AI tutor that can help them understand difficult material without manually assembling context or planning every prerequisite.

Core use cases:

1. **Import study material:** Add one or more files and see their extraction status.
2. **Organize sources:** Keep files in a persistent local library and optionally group/select them for a session.
3. **Start from a goal:** State something such as “give me a solid introduction to differential forms” or “prepare me for this exam.”
4. **Probe understanding:** Answer graded multiple-choice questions, including “I don't know,” so the tutor can estimate prerequisites.
5. **Review and edit a plan:** Inspect a dependency graph, then approve, reorder, skip, or expand nodes.
6. **Learn incrementally:** Receive one explanation/reasoning step at a time, ask questions, pause, or continue.
7. **Practice:** Answer periodic multiple-choice checks and choose whether to receive a hint, explanation, retry, or skip.
8. **Inspect evidence:** Open the exact excerpt from a file that supports a claim.
9. **Resume later:** Reopen a session and retain plan position, prior answers, and mastery updates.

## 7. MVP user experience

### 7.1 Local setup

The app runs on the Raspberry Pi with Python 3.11 and must start through one documented development command. Set up the environment once:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Start the app with a single command:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Binding to `0.0.0.0` makes the app reachable on the LAN, e.g. `http://<pi-ip>:8000` from a laptop or phone. The exact interpreter name on the chosen Raspberry Pi OS release and the documented way to discover the Pi's LAN address (`hostname -I`, mDNS hostname, or router DHCP list) must be confirmed during implementation. Optional `HOST` and `PORT` environment variables may override the bind address and port, but the defaults (`0.0.0.0`, `8000`) must remain LAN-reachable. The app should read these variables from a local `.env` file:

```env
BROT_BASE_URL=http://127.0.0.1:8877/v1
BROT_API_KEY=<required secret>
BROT_MODEL=deepseek/deepseek-v4-pro
```

The `.env` file must be excluded from version control. The API key must remain server-side and must not be exposed to browser JavaScript.

The UI should show a clear configuration/connection error if the variables are missing or the endpoint cannot be reached. A small connection test or health indication is desirable.

### 7.2 Study workspace

The main screen should be a focused workspace with:

- A file library/sidebar showing uploaded files and extraction status.
- Controls to add files, remove files, retry extraction, and select files for the current session.
- A session/chat transcript.
- A composer for normal questions and instructions.
- Streaming assistant output.
- A stop-generation control.
- Clear loading, retry, and error states.
- A visible current phase or status such as Probe, Plan, Teach, or Check.
- A plan panel showing the current learning objective and Mermaid dependency graph.
- Study controls such as continue, pause, ask a question, request a hint, retry, reveal explanation, skip, and end session where applicable.

The UI should stay simple and readable. It should prioritize study flow over configuration screens or a large dashboard.

### 7.3 Flexible conversation

The interaction should not force the user through a rigid wizard. The user can type a question or instruction at any point. The tutor should preserve the structured state internally while allowing natural transitions such as:

- Asking a clarification question during a teaching step.
- Pausing a quiz to discuss a misconception.
- Requesting a different explanation.
- Revisiting an earlier plan node.
- Adding or selecting a source during a session.
- Asking for a source-only answer instead of continuing the lesson.

The agent remains collaborative: it may propose the next action, but the user can intervene and control the plan.

## 8. Agent behavior and state model

The implementation should model a session with explicit state, even if the user experiences it as chat. Suggested phases:

1. **Setup:** Goal, selected sources, grounding mode, learner preferences, and constraints are available.
2. **Probe:** The tutor asks broad-to-specific graded multiple-choice questions to estimate relevant prerequisite knowledge. It should accept “I don't know.”
3. **Plan:** The tutor proposes a dependency-aware path from the estimated current understanding to the user's goal. It exposes the plan as a Mermaid graph.
4. **Teach:** The tutor explains one manageable reasoning step or concept at a time and waits for interaction.
5. **Check:** The tutor periodically asks a multiple-choice question. Results update learner calibration and may cause a plan adjustment.
6. **Remediate:** If needed, the tutor offers explanation, hint, retry, reveal, or skip controls and can branch into prerequisite material.
7. **Complete or continue:** The session can end, resume, or continue toward the goal.

These phases should be resumable and should not require every session to use every phase. For example, a quick document question may go directly to answer mode, while a serious learning goal should normally use probe and plan.

### 8.1 Probing requirements

- Questions should be relevant to the stated goal and selected source material.
- The tutor should begin broadly and narrow toward prerequisite boundaries rather than asking an arbitrary long quiz.
- Each question should have clearly labeled answer choices and one expected answer where appropriate.
- The user must be able to select “I don't know.”
- The UI should provide immediate feedback after submission.
- Probe results should be persisted as part of the session and contribute to the mastery map.
- The tutor should avoid treating a lucky answer as proof of deep mastery; confidence should be represented as an estimate.

### 8.2 Plan requirements

- The plan should identify the goal, prerequisite concepts, major nodes, and a sensible sequence.
- The user can approve, reorder, skip, or expand plan nodes.
- The plan should update when quiz results reveal a gap or when the user changes the goal.
- The plan should be rendered as Mermaid where possible.
- If Mermaid rendering fails, the UI must show a readable text/outline fallback and must not lose the plan.
- Plan content must be treated as model-generated guidance, not an authoritative proof of correctness.

### 8.3 Teaching requirements

- Default to one reasoning step or concept per assistant turn.
- Avoid racing through the whole plan in one response.
- Explain prerequisites when the learner is not ready for the next step.
- Permit the user to ask questions before moving forward.
- Use source excerpts where they support the explanation.
- Clearly distinguish:
  - What is directly supported by an attached source.
  - What is a synthesis or explanation based on the sources.
  - What comes from general model knowledge.
  - What is uncertain or unresolved.

### 8.4 Quiz and feedback requirements

V1 quizzes use multiple-choice questions only.

After a response or “I don't know,” the user should be able to choose among:

- Explain the answer.
- Give a hint.
- Retry.
- Reveal the answer/explanation.
- Skip and return to the plan.
- Continue after understanding the feedback.

The agent should use the selected action and result to decide whether to continue, remediate, or adjust the plan. The user—not the agent alone—controls the response to an incorrect answer.

### 8.5 Uncertainty and disagreement

The tutor must be conservative:

- Do not present unsupported claims as verified facts.
- If attached sources are insufficient, say so.
- If sources disagree, identify the disagreement and show the relevant excerpts where possible.
- If the model is uncertain, disclose the uncertainty and ask whether to continue or investigate through available sources.
- Do not silently invent citations, page numbers, quotes, or source support.
- A missing source citation is preferable to a fabricated one.

V1 does not require external research, but the internal design should leave room for a future verifier or research stage.

## 9. Source ingestion and retrieval

### 9.1 Supported formats

The first version should target common study formats:

- PDF.
- DOCX.
- PPTX.
- Markdown.
- Plain text.
- Common image formats where reliable local extraction is available.

For PDFs, DOCX, and PPTX, preserve page or slide boundaries where possible. For Markdown and text, preserve headings and line ranges. For images, preserve the filename and image-level reference; OCR or multimodal extraction may be used if supported by the chosen local implementation and endpoint.

### 9.2 Ingestion flow

1. User chooses one or more local files.
2. The server stores them in the local application data directory.
3. The server extracts text and source metadata.
4. The server reports success, partial success, or failure for each file.
5. The extracted text is split into manageable chunks using simple deterministic chunking for v1.
6. Each chunk stores enough metadata to locate its source:
   - File ID and display name.
   - Page, slide, section, or line range where available.
   - Chunk index.
   - Extracted text.
7. Relevant chunks are selected for each request based on the current user message, goal, plan node, and selected files.
8. Only the selected context is included in the model request.

The first version does not need a vector database or sophisticated semantic index. Chunking and simple retrieval should be designed behind an interface so better retrieval can be added later.

### 9.3 Extraction failures

- Unsupported formats must be clearly identified before or during upload.
- Partial extraction must show which content was recovered.
- A failed file must not silently enter the tutor context.
- The user can retry or remove a failed file.
- The tutor should mention when a selected source was not successfully indexed.
- If a file is image-only or scanned and text cannot be extracted, the UI should state that OCR/multimodal reading is unavailable or incomplete rather than pretending the file was read.

### 9.4 Grounding modes

Grounding is user-controlled per session:

- **Strict source-only mode:** Prefer only selected source excerpts. If the answer is not supported, say that the available sources are insufficient.
- **Grounded plus model knowledge mode:** Use selected sources as the primary context and supplement with general model knowledge, explicitly labeling the distinction.

The selected mode must be visible in the session UI and recorded in the session history.

### 9.5 Citations and inspectable excerpts

Responses should cite source claims inline when source material is used. A citation should identify at least the filename and the best available location, such as page, slide, section, or line range.

Selecting a citation should open or reveal an excerpt panel containing:

- The exact extracted text used.
- Filename.
- Page/slide/section/line metadata.
- Enough surrounding context to understand the passage.

The system must not fabricate locations. If precise location metadata is unavailable, it should use a lower-confidence reference such as file and chunk number and make that limitation visible.

## 10. Privacy and transmission controls

The app is local-first, but the configured model endpoint still receives the prompts and any context included in requests.

Requirements:

- Files, extracted text, sessions, and progress remain in local server storage by default.
- The API key is only used by the local backend.
- The application must not upload the entire library automatically.
- The user can control which files are selected for a session.
- Before sending context, the app should make the selected source scope apparent; a preview/control for the excerpts being sent is preferred.
- Only the prompt, relevant selected excerpts, and required session context should be transmitted.
- The UI should make it clear that “local app” does not mean the configured model endpoint never receives selected study content.
- No telemetry or cloud analytics is required for v1.

## 11. Persistence and learner profile

Use local server-managed storage rather than browser-only persistence.

Persist at minimum:

### File library

- File metadata and stable ID.
- Original filename and display name.
- MIME type and size.
- Storage path or local storage reference.
- Ingestion/extraction status and error details.
- Extracted text/chunks and source-location metadata.
- Created/updated timestamps.

### Sessions

- Session ID, title, goal, and timestamps.
- Selected source IDs.
- Grounding mode.
- Conversation messages and streaming-completed content.
- Current phase and current plan node.
- Plan graph/outline and user edits.
- Probe and quiz questions, selected answers, feedback actions, and results.
- Source citations and excerpt references.

### Learner profile and mastery map

The user selected a detailed mastery map. V1 should track topic-level estimates such as:

- Topic/concept identifier and label.
- Estimated mastery/confidence.
- Evidence source: probe, quiz, user statement, or session outcome.
- Recent correctness and “I don't know” results.
- Last assessed timestamp.
- Optional notes or learner preference for that topic.

The mastery map should influence future probing and plan proposals, but the UI can initially expose it through session context and a basic progress view rather than a large analytics dashboard.

### Learner preferences

Provide a place to retain basic teaching preferences alongside the detailed map, such as preferred depth, pacing, and explanation style. The user must be able to edit or clear this data.

## 12. Backend and model integration requirements

**Concrete stack:** Python 3.11, FastAPI, served by uvicorn on the Pi. The frontend is built as static files served by the same FastAPI process (see §17.1), so a single uvicorn process handles both the JSON API and the UI — no separate frontend dev server or proxy is required. This keeps the one-command workflow trivial and the memory footprint small on a 4–8 GB device.

The local backend should provide a small application API for:

- File upload/list/detail/delete/retry ingestion.
- Session create/list/load/update.
- Chat or tutor-turn streaming.
- Probe/quiz answer submission.
- Plan approval and edits.
- Citation/excerpt retrieval.
- Learner profile and mastery retrieval/update.
- BROT connection status.

The backend should call the OpenAI-compatible BROT endpoint using the configured base URL, API key, and model. It should normalize provider errors into useful UI errors and avoid returning the secret key to the client.

The implementation must verify the endpoint's actual behavior before relying on:

- Whether the correct route is `/chat/completions` under the supplied `/v1` base URL.
- Streaming support and event format.
- The accepted model name.
- Context-window and output limits.
- Tool/function-calling support, if considered later.
- Whether multimodal image input is supported.

The initial design should not require tool calling. The server can orchestrate ingestion, retrieval, session state, and quiz state itself and pass structured instructions/context in messages.

## 13. Error handling and resilience

The app should handle gracefully:

- Missing or invalid environment configuration.
- BROT unavailable or returning authentication/rate-limit errors.
- A dropped streaming connection.
- Model output that is malformed or missing expected quiz/plan structure.
- File type or extraction errors.
- Duplicate files.
- Large files or context exceeding model limits.
- **Slow local model inference:** the BROT endpoint runs on the same Pi and may respond slowly, especially for long generations; use generous client/server timeouts, stream responses, and keep loading/stop controls prominent in the UI.
- A runtime or extraction dependency without an ARM64/aarch64 Python 3.11 wheel must fail fast at setup with a clear, actionable message — not surface as a mid-session runtime traceback.
- Mermaid rendering errors.
- Interrupted sessions and browser refreshes.

A partially completed assistant response should not be persisted as complete unless the stream finishes. The user should be able to retry a failed model request without duplicating a quiz submission or corrupting the session state.

## 14. Security and local deployment

- Bind the server to `0.0.0.0` by default so the app is reachable from any device on the local network, e.g. `http://<pi-ip>:8000`.
- Do not log the API key or full sensitive document contents by default.
- Validate uploaded filenames and avoid path traversal.
- Store uploads outside the publicly served static directory.
- Enforce configurable file-size and total-storage limits.
- Treat extracted document text as untrusted model context and guard against prompt injection from documents.
- Clearly separate document instructions from system/application instructions.
- Ensure the browser cannot directly read the `.env` file.

LAN exposure is an intentional v1 trade-off. The app ships with no authentication, so anyone who can reach the Pi's IP on the local network can open the UI and use the configured model endpoint. Treat the app as a trusted-network-only service in v1 and document this clearly in the README. The in-scope mitigations remain: the API key stays server-side and is never delivered to the browser, and usage is bounded by the single local endpoint. An optional shared access token is a candidate follow-up if the Pi is ever exposed beyond a trusted LAN (see §17).

## 15. Acceptance criteria for the first implementation

### Setup

- [ ] A fresh checkout can be started on a Raspberry Pi (64-bit ARM64) with Python 3.11 using one documented local command.
- [ ] The app reads the three `BROT_*` environment variables.
- [ ] Missing configuration and unreachable endpoint states are visible and actionable.
- [ ] The API key never appears in browser-delivered source or normal UI responses.
- [ ] After starting, the app is reachable from another device on the LAN at `http://<pi-ip>:8000`.
- [ ] Every runtime and extraction dependency installs for ARM64/aarch64 Python 3.11; a missing wheel produces a clear setup error rather than a runtime traceback.

### Files

- [ ] A user can upload at least PDF, DOCX, PPTX, Markdown, and plain-text study files.
- [ ] Each file shows extraction status and errors.
- [ ] Extracted content is persisted locally and can be reused in a later session.
- [ ] The user can select which files are active for a session.
- [ ] Simple chunking preserves file and location metadata.
- [ ] Failed or unsupported extraction is explicit and never silently treated as successful.

### Learning workflow

- [ ] A user can state a learning goal and begin a flexible session.
- [ ] The tutor can run a relevant multiple-choice probe and accept “I don't know.”
- [ ] The tutor produces a dependency-aware plan with a Mermaid graph.
- [ ] The user can approve, reorder, skip, or expand plan nodes.
- [ ] Teaching defaults to one incremental step at a time.
- [ ] The user can pause, ask questions, request a hint, retry, reveal feedback, skip, and continue.
- [ ] Periodic quiz results can change the next teaching step or plan.
- [ ] The session can resume after refresh or later reopening.

### Grounding and trust

- [ ] The user can choose strict source-only or grounded-plus-knowledge mode.
- [ ] Responses distinguish source-backed content from model synthesis or general knowledge.
- [ ] Source citations identify file and location when available.
- [ ] Clicking a citation reveals the exact extracted excerpt used.
- [ ] The tutor discloses insufficient evidence, uncertainty, and source disagreement.
- [ ] The app does not fabricate citations or claim to have read failed files.

### Persistence

- [ ] Files, sessions, plan state, quiz results, preferences, and mastery data are stored locally on the server.
- [ ] Topic-level mastery estimates are updated from probe and quiz outcomes.
- [ ] The user can clear learner data and remove local files.

## 16. Suggested implementation phases

### Phase 1 — Local shell and BROT connectivity

- Create the Python 3.11 virtual environment and install FastAPI, uvicorn, and project dependencies.
- Establish the one-command start command (`uvicorn app.main:app --host 0.0.0.0 --port 8000`).
- Add environment configuration and server-side OpenAI-compatible client.
- Verify a basic streamed chat request against the configured endpoint.
- Verify the app is reachable from another device on the LAN at `http://<pi-ip>:8000`.
- Add normalized error handling.

### Phase 2 — File library and extraction

- Add local file storage and metadata persistence.
- Implement target document extraction and simple chunking.
- Add source-location metadata and extraction status UI.

### Phase 3 — Grounded chat and citations

- Add file selection per session.
- Select relevant chunks and include them in model context.
- Add strict and grounded-plus-knowledge modes.
- Add inspectable excerpt citations.

### Phase 4 — Structured learning loop

- Add session state, probe questions, and multiple-choice answer handling.
- Add plan generation, Mermaid display, and user plan controls.
- Add incremental teaching and periodic checks.

### Phase 5 — Persistence and mastery

- Persist resumable sessions.
- Track learner preferences and detailed topic-level mastery.
- Use results to influence future probing and plans.
- Add data management and clear/reset controls.

## 17. Open implementation decisions

These items were not specified by the user and should be resolved during implementation without changing the product direction:

1. RESOLVED — backend is FastAPI served by uvicorn on Python 3.11, with the frontend served as static files from the same FastAPI process; dependencies are managed with `pip` in a Python 3.11 virtual environment (`requirements.txt`). Rationale: a single uvicorn process serves API and UI, keeping the one-command workflow simple on a low-powered Pi, and `pip` + `venv` add no tooling beyond Python 3.11 itself. `uv` is a faster-install fallback if Python 3.11 bootstrap on the Pi proves awkward (to be verified during implementation).
2. The local database/storage technology and application-data directory convention. Pi default: application data in `~/.local/share/sage` (XDG convention on Raspberry Pi OS), with uploads in a non-public subdirectory; a single local SQLite database is presumed unless a later phase requires more.
3. Exact default upload size, per-file chunk size, total storage limit, and context-budget policy.
4. The extraction libraries for PDF, DOCX, PPTX, and optional OCR — restricted to libraries with ARM64/aarch64 Python 3.11 wheels. Candidate set: `pymupdf` (PDF), `python-docx` (DOCX), `python-pptx` (PPTX). ARM64 Python 3.11 wheel availability for the final set (including any OCR choice) must be verified during implementation.
5. Whether the running BROT endpoint supports streaming and image input.
6. The exact structured format used for model-generated plans and quiz questions, plus validation/recovery behavior.
7. Whether citations are generated by the model, derived from retrieved chunk IDs, or both. Retrieved chunk IDs should be authoritative for inspectability.
8. Whether a basic mastery-map view is included in the initial UI or only used internally until a later phase.
9. How duplicate files are detected and whether replacing a file should invalidate dependent chunks and citations.
10. RESOLVED — same-origin: the FastAPI/uvicorn process serves both the API and the static frontend, so no separate development proxy is needed.
11. Default bind port (recommended `8000`) and whether `HOST`/`PORT` environment overrides ship in v1 (proposed: yes, with `0.0.0.0`/`8000` as defaults).
12. Whether v1 adds an optional shared access token given LAN exposure, or ships trusted-network-only with README documentation of the risk (§14).
13. Confirm the application-data directory — default `~/.local/share/sage` versus a system-level location — tied to whether the app runs as a normal user or as a systemd service.
14. How the BROT model endpoint is started on the Pi (manual command vs. a systemd service alongside the app) and how users discover the Pi's address (`hostname -I`, mDNS hostname, or router DHCP list).

## 18. Explicitly deferred future enhancements

- Semantic/vector retrieval and reranking.
- External web research and dedicated fact-checking sub-agents.
- SVG/diagram generation and visual verification sub-agents.
- Rich question types, written-answer grading, and practice problem execution.
- Calendar planning, spaced repetition, and reminders.
- Multi-user accounts and remote synchronization.
- Additional model providers or model switching from the UI.
- Export to Markdown, Obsidian, or a formal study notebook.
