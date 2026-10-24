# Requirements Document

## Introduction

Boston Bill Party ("No overcharging without representation") is a multi-agent AI patient advocate for Massachusetts residents. A user uploads a medical bill as a PDF; the system extracts line items into a structured schema, audits them for likely billing errors, checks patient-rights issues against a small retrieval knowledge base, filters out weak findings, and drafts a plain-language summary plus an editable appeal letter in the user's chosen language. The user always reviews content before any send action. The system operates exclusively on synthetic data and never processes real patient data.

The backend is a Python FastAPI service orchestrating a LangGraph pipeline of five nodes (extractor, auditor, rights_checker, skeptic, writer). PDF text is read with pdfplumber and structured by a configurable LLM into a Pydantic `ExtractedBill` schema, backed by a deterministic sum check. The frontend is a Next.js + Tailwind application with three screens (upload, live agent progress, results with appeal editor) and browser-based voice Q&A. A set of ten synthetic bills with planted errors serves as the evaluation set.

Delivery is prioritized as an MVP (extractor → auditor → writer → UI) followed by the rights_checker, skeptic, voice Q&A, and evaluation set, scoped to be buildable by a single developer in approximately seven hours.

## Glossary

- **System**: The complete Boston Bill Party application, comprising the FastAPI backend, LangGraph pipeline, and Next.js frontend.
- **Backend**: The Python FastAPI service that exposes HTTP endpoints and runs the LangGraph pipeline.
- **Frontend**: The Next.js + Tailwind web application presenting the upload, progress, and results screens.
- **Pipeline**: The LangGraph graph that executes the Extractor, Auditor, Rights_Checker, Skeptic, and Writer nodes in sequence.
- **Extractor**: The pipeline node that converts raw PDF text into a structured `ExtractedBill` object using the LLM.
- **Auditor**: The pipeline node that inspects extracted line items for likely billing errors.
- **Rights_Checker**: The pipeline node that identifies possible patient-rights issues using the RAG knowledge base.
- **Skeptic**: The pipeline node that filters out weak or low-confidence findings before drafting.
- **Writer**: The pipeline node that drafts the plain-language summary and the appeal letter in the selected language.
- **LLM_Provider**: The large language model service used by the Extractor, Rights_Checker, Skeptic, and Writer, configured through environment variables.
- **ExtractedBill**: The Pydantic schema representing a parsed medical bill, including provider metadata, a list of line items, and totals.
- **Line_Item**: A single charge entry within an `ExtractedBill`, including description, billing code, quantity, unit price, and amount.
- **Finding**: A single detected issue produced by the Auditor or Rights_Checker, including a type, description, evidence, and confidence score.
- **Sum_Check**: The deterministic calculation that compares the sum of Line_Item amounts against the stated bill total.
- **RAG_Knowledge_Base**: The small curated collection of Massachusetts patient-rights reference documents used for retrieval by the Rights_Checker.
- **Appeal_Letter**: The editable draft letter contesting billing errors or rights issues, produced by the Writer.
- **Summary**: The plain-language explanation of the bill analysis produced by the Writer.
- **Supported_Language**: One of English, Spanish, Portuguese, or Haitian Creole.
- **Voice_QA**: The browser-based feature that accepts spoken questions and reads spoken answers using the Web Speech API.
- **Eval_Set**: The collection of ten synthetic medical bills with planted errors used to measure detection quality.
- **Confidence_Score**: A numeric value between 0 and 1 assigned to each Finding indicating the estimated likelihood that the Finding is valid.
- **Relevance_Score**: A numeric value between 0 and 1 indicating how relevant a retrieved passage is to a query.

## Requirements

### Requirement 1: Upload a medical bill PDF

**User Story:** As a Massachusetts resident, I want to upload a medical bill as a PDF, so that the system can analyze it for errors.

#### Acceptance Criteria

1. WHEN a user submits a file with a PDF content type through the upload screen, THE Backend SHALL accept the file and begin a new analysis session.
2. IF a user submits a file that is not a PDF, THEN THE Backend SHALL reject the file, retain no analysis session, and return an error message identifying the accepted format as PDF.
3. IF a user submits a PDF file larger than 10 megabytes or smaller than 1 byte, THEN THE Backend SHALL reject the file, retain no analysis session, and return an error message stating the accepted size range is 1 byte to 10 megabytes.
4. WHEN a user selects a Supported_Language on the upload screen, THE Frontend SHALL record the selected language for use in the analysis session.
5. IF no language is selected before upload, THEN THE Frontend SHALL default the analysis session language to English.
6. IF a submitted file has a PDF content type but cannot be parsed as a valid PDF document, THEN THE Backend SHALL reject the file, retain no analysis session, and return an error message indicating the file is corrupt or unreadable.
7. WHEN the Backend accepts a file and begins a new analysis session, THE Backend SHALL return a confirmation to the Frontend that includes the analysis session identifier within 5 seconds of receiving the file.

### Requirement 2: Extract bill text and structure it

**User Story:** As a user, I want the system to read my bill and organize its charges, so that each charge can be reviewed individually.

#### Acceptance Criteria

1. WHEN an analysis session begins, THE Extractor SHALL read the text content of the uploaded PDF using pdfplumber, concatenating text from all pages in page order.
2. WHEN raw PDF text is available, THE Extractor SHALL use the LLM_Provider to produce an ExtractedBill object that conforms to the Pydantic ExtractedBill schema, populating the provider metadata, line items, and totals fields.
3. WHERE the PDF contains one or more charge entries, THE Extractor SHALL represent each charge entry as a Line_Item containing description, billing code, quantity, unit price, and amount fields.
4. IF a charge entry is missing a value for the billing code, quantity, unit price, or amount field, THEN THE Extractor SHALL set that field to null and retain the remaining populated fields of the Line_Item.
5. WHEN the ExtractedBill is produced, THE Extractor SHALL represent each monetary amount and unit price as a non-negative decimal value with exactly two decimal places and each quantity as a non-negative numeric value.
6. IF the LLM_Provider output does not conform to the ExtractedBill schema, THEN THE Extractor SHALL return a validation error that names each nonconforming field and the reason it failed validation, without producing a partial ExtractedBill object.
7. IF the PDF contains no extractable text after reading all pages, THEN THE Extractor SHALL return an error message stating that no readable text was found and SHALL NOT invoke the LLM_Provider.
8. IF the LLM_Provider does not return a response within 60 seconds, THEN THE Extractor SHALL abort the extraction and return an error message indicating that bill extraction timed out.

### Requirement 3: Deterministic sum check

**User Story:** As a user, I want the math on my bill verified independently, so that I can trust arithmetic errors are caught reliably.

#### Acceptance Criteria

1. WHEN an ExtractedBill is produced, THE Backend SHALL compute the Sum_Check by adding the amount of every Line_Item, treating an ExtractedBill with zero Line_Items as a computed total of 0.00.
2. IF the Sum_Check total differs from the stated bill total by more than 0.01 dollars, THEN THE Auditor SHALL record a Finding of type math error that includes the computed total and the stated total.
3. THE Backend SHALL perform the Sum_Check using exact decimal arithmetic rather than the LLM_Provider.
4. WHEN the Backend computes the Sum_Check on the same ExtractedBill more than once, THE Backend SHALL produce an identical computed total on every execution.
5. IF any Line_Item has an amount that is missing or cannot be parsed as a decimal value, THEN THE Auditor SHALL record a Finding of type math error that identifies the affected Line_Item, and THE Backend SHALL exclude that Line_Item from the Sum_Check total without aborting the computation.
6. IF the ExtractedBill has no stated bill total, THEN THE Auditor SHALL record a Finding of type math error indicating that the stated total is absent, and THE Backend SHALL retain the computed Sum_Check total.

### Requirement 4: Audit for billing errors

**User Story:** As a user, I want the system to flag likely billing errors, so that I know which charges to question.

#### Acceptance Criteria

1. WHEN an ExtractedBill is available, THE Auditor SHALL inspect every Line_Item in the ExtractedBill for duplicate charges, math errors, and suspicious billing codes.
2. WHEN two or more Line_Items share an identical billing code and an identical description, THE Auditor SHALL record one Finding of type duplicate charge that references all affected Line_Items.
3. WHEN a Line_Item amount differs from its quantity multiplied by its unit price by more than 0.01 currency units, THE Auditor SHALL record a Finding of type math error that references the affected Line_Item.
4. WHERE a Line_Item billing code is absent from the set of recognized billing codes, THE Auditor SHALL record a Finding of type suspicious code that references the affected Line_Item.
5. IF a Line_Item has no billing code, THEN THE Auditor SHALL record a Finding of type suspicious code that references the affected Line_Item.
6. THE Auditor SHALL assign to each Finding a Confidence_Score that is a decimal value in the inclusive range 0 to 1.
7. IF the ExtractedBill contains zero Line_Items, THEN THE Auditor SHALL complete the audit, record no Findings, and produce an empty set of Findings as output.

### Requirement 5: Check patient-rights issues using retrieval

**User Story:** As a user, I want the system to identify possible violations of my patient rights, so that my appeal can cite relevant protections.

#### Acceptance Criteria

1. WHEN the Auditor completes, THE Rights_Checker SHALL retrieve up to 10 reference passages from the RAG_Knowledge_Base within 15 seconds, using the ExtractedBill and Auditor Findings as the query context.
2. WHERE a retrieved passage has a Relevance_Score of at least 0.7 for a potential patient-rights issue, THE Rights_Checker SHALL record a Finding of type rights issue that cites that supporting passage.
3. WHEN the Rights_Checker records a rights issue Finding, THE Rights_Checker SHALL assign a Confidence_Score in the inclusive range 0 to 1 to that Finding.
4. IF no retrieved passage reaches a Relevance_Score of at least 0.7 for a potential issue, THEN THE Rights_Checker SHALL omit that issue from the Findings.
5. IF retrieval from the RAG_Knowledge_Base fails or returns no passages within the 15-second timeout, THEN THE Rights_Checker SHALL record no rights issue Findings and return a status indicating that the rights check could not be completed, while preserving the Auditor Findings.

### Requirement 6: Filter weak findings

**User Story:** As a user, I want weak or uncertain findings removed, so that I focus only on credible issues.

#### Acceptance Criteria

1. WHEN the Rights_Checker signals that its Finding set is ready, THE Skeptic SHALL evaluate every Finding for supporting evidence and confidence before passing any Finding to the Writer.
2. IF a Finding has a Confidence_Score below 0.5 on the 0.0 to 1.0 scale, THEN THE Skeptic SHALL exclude that Finding from the set passed to the Writer.
3. IF a Finding references fewer than one Line_Item and fewer than one knowledge-base passage, THEN THE Skeptic SHALL exclude that Finding from the set passed to the Writer.
4. WHERE a Finding has a Confidence_Score of at least 0.5 and references at least one Line_Item or at least one knowledge-base passage, THE Skeptic SHALL include that Finding in the set passed to the Writer.
5. IF no Finding meets the inclusion conditions, THEN THE Skeptic SHALL pass an empty set of Findings to the Writer.

### Requirement 7: Draft summary and appeal letter

**User Story:** As a user, I want a plain-language summary and a drafted appeal letter, so that I can understand the issues and contest them.

#### Acceptance Criteria

1. WHEN the Skeptic completes with at least one retained Finding, THE Writer SHALL generate a Summary containing one entry per retained Finding, where each entry identifies the Finding and describes its issue in plain language.
2. WHEN the Skeptic completes with at least one retained Finding, THE Writer SHALL generate an Appeal_Letter containing one contesting statement per retained Finding.
3. THE Writer SHALL produce the Summary and the Appeal_Letter in the Supported_Language selected for the analysis session.
4. IF no Findings are retained, THEN THE Writer SHALL generate a Summary stating that no credible billing errors or rights issues were detected and SHALL NOT generate an Appeal_Letter.
5. IF the Writer fails to generate the Summary or the Appeal_Letter, THEN THE Writer SHALL return an error indicating generation failed and SHALL NOT produce a partial Summary or partial Appeal_Letter.

### Requirement 8: Review and edit before sending

**User Story:** As a user, I want to review and edit the appeal letter before it is sent, so that I remain in control of what is communicated.

#### Acceptance Criteria

1. WHEN the Writer completes, THE Frontend SHALL display the Summary and the Appeal_Letter on the results screen within 2 seconds of completion.
2. IF the Writer fails to produce an Appeal_Letter, THEN THE Frontend SHALL display an error message indicating generation failed and SHALL provide a retry action without discarding the Summary.
3. THE Frontend SHALL present the Appeal_Letter in an editable text field that accepts user modifications of up to 20,000 characters.
4. WHILE the user is editing the Appeal_Letter, THE Frontend SHALL retain all user modifications in the editable text field until the user confirms a send or export action.
5. THE Frontend SHALL require an explicit user confirmation action, consisting of the user activating a dedicated confirm control, before any send or export of the Appeal_Letter.
6. THE System SHALL NOT transmit the Appeal_Letter to any external recipient without the explicit user confirmation action defined in criterion 5.
7. WHEN the user activates the confirm control, THE System SHALL transmit the current contents of the editable text field, including any user modifications, as the Appeal_Letter.

### Requirement 9: Live agent progress

**User Story:** As a user, I want to see each agent's progress during analysis, so that I understand what the system is doing.

#### Acceptance Criteria

1. WHILE the Pipeline is executing, THE Frontend SHALL display the name of the currently active node from the set {Extractor, Auditor, Rights_Checker, Skeptic, Writer}, updating the displayed node within 2 seconds of the Backend emitting a progress update.
2. WHEN a Pipeline node completes, THE Backend SHALL emit a progress update identifying the completed node name and its completion status.
3. WHEN the Backend emits a progress update, THE Frontend SHALL mark the identified node as completed and display the count of completed nodes out of the total of 5 nodes.
4. IF a Pipeline node returns an error, THEN THE Frontend SHALL display an error message identifying the node that failed and indicating that analysis has stopped, while retaining the progress state of all previously completed nodes.
5. IF the Frontend receives no progress update for 60 seconds while the Pipeline is executing, THEN THE Frontend SHALL display a message indicating that progress updates have been interrupted.

### Requirement 10: Voice question and answer

**User Story:** As a user, I want to ask questions about my bill by voice and hear the answers, so that I can interact without typing.

#### Acceptance Criteria

1. WHERE the browser supports the Web Speech API, THE Frontend SHALL display a Voice_QA control on the results screen within 1 second of the results screen rendering.
2. WHEN a user activates the Voice_QA control and speaks a question, THE Frontend SHALL transcribe the spoken question to text and display the transcribed text to the user before submission.
3. WHEN the Frontend has transcribed a spoken question of between 1 and 500 characters, THE Frontend SHALL submit the transcribed text to the Backend.
4. IF the Frontend captures no speech within 10 seconds of the Voice_QA control being activated, THEN THE Frontend SHALL stop listening and display a message indicating that no speech was detected while retaining the control in an inactive state.
5. IF speech transcription fails or produces an empty result, THEN THE Frontend SHALL display an error message indicating that the question could not be understood and SHALL NOT submit a request to the Backend.
6. WHEN the Backend returns an answer to a Voice_QA question, THE Frontend SHALL read the answer aloud using the Web Speech API and SHALL display the answer text on the results screen.
7. IF the Backend returns an error or does not respond within 30 seconds of a Voice_QA submission, THEN THE Frontend SHALL display an error message indicating that the answer could not be retrieved and SHALL retain any prior displayed answer.
8. WHERE the browser does not support the Web Speech API, THE Frontend SHALL hide the Voice_QA control and provide a text input alternative for submitting questions to the Backend.

### Requirement 11: Configurable LLM provider

**User Story:** As a developer, I want the LLM provider configured through environment variables, so that I can switch providers without changing code.

#### Acceptance Criteria

1. WHEN the Backend starts, THE Backend SHALL read the LLM_Provider configuration values from the .env file.
2. IF any required LLM_Provider configuration value is missing or empty, THEN THE Backend SHALL abort startup without initializing any Extractor, Rights_Checker, Skeptic, or Writer node, and SHALL report an error identifying each missing value by its environment variable name.
3. IF the LLM_Provider configuration value naming the provider does not match one of the supported providers, THEN THE Backend SHALL abort startup and SHALL report an error identifying the unsupported provider value and listing the supported providers.
4. WHEN the Backend completes startup with a valid LLM_Provider configuration, THE Backend SHALL route all Extractor, Rights_Checker, Skeptic, and Writer LLM calls to the configured LLM_Provider without requiring a code change to switch providers.
5. WHERE the LLM_Provider configuration specifies a per-node model or parameter override for the Extractor, Rights_Checker, Skeptic, or Writer, THE Backend SHALL apply that override to the corresponding node's LLM calls and SHALL apply the shared configured values to all nodes without a per-node override.

### Requirement 12: Synthetic data only

**User Story:** As a stakeholder, I want the system to use only synthetic data, so that no real patient data is at risk.

#### Acceptance Criteria

1. THE System SHALL process only synthetic medical bill data and SHALL NOT ingest, store, or transmit any real patient data.
2. IF input data presented to the System is not marked as synthetic, THEN THE System SHALL reject the input and SHALL produce an error indication that non-synthetic data was supplied, without processing the input.
3. THE Eval_Set SHALL contain exactly ten synthetic medical bills, each containing one or more planted billing errors, with a minimum of one and a maximum of ten planted errors per bill.
4. WHERE a synthetic bill in the Eval_Set contains a planted error, THE Eval_Set SHALL record, for each planted error, the expected Finding type and a unique identifier linking the planted error to its location within that bill.
5. IF a synthetic bill in the Eval_Set contains a planted error without a recorded expected Finding type, THEN THE Eval_Set SHALL be treated as invalid and THE System SHALL produce an error indication identifying the bill with the missing expected Finding type.

### Requirement 13: Evaluation of detection quality

**User Story:** As a developer, I want to measure detection quality against the eval set, so that I can verify the pipeline catches planted errors.

#### Acceptance Criteria

1. WHEN the evaluation is run over the Eval_Set, THE Backend SHALL execute the Pipeline on each synthetic bill in the Eval_Set exactly once and record the retained Findings produced for each synthetic bill.
2. IF the Eval_Set contains zero synthetic bills when the evaluation is run, THEN THE Backend SHALL terminate the evaluation without executing the Pipeline and produce an error indication reporting that the Eval_Set is empty.
3. IF the Pipeline fails to complete for a synthetic bill, THEN THE Backend SHALL record that synthetic bill as failed, continue executing the Pipeline on the remaining synthetic bills in the Eval_Set, and include the count of failed synthetic bills in the final report.
4. WHEN the Pipeline completes for a synthetic bill, THE Backend SHALL match each retained Finding against each planted error recorded for that bill and classify each planted error as detected when at least one retained Finding matches it or missed when no retained Finding matches it.
5. WHEN the Pipeline completes for a synthetic bill, THE Backend SHALL classify each retained Finding that matches no planted error for that bill as a false positive.
6. WHEN the evaluation completes, THE Backend SHALL report the total count of planted errors detected, the total count of planted errors missed, and the total count of false positive Findings aggregated across all synthetic bills in the Eval_Set.
