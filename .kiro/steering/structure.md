# Project structure

```
boston-bill-party/
├── .kiro/
│   ├── specs/boston-bill-party/   # requirements.md, design.md, tasks.md (the spec)
│   ├── steering/                  # product.md, tech.md, structure.md (this file)
│   └── hooks/                     # agent hooks (pytest on pipeline saves)
├── backend/                       # FastAPI service (run from this directory)
│   ├── main.py                    # app, routes, SSE, upload validation
│   ├── config.py                  # .env loading/validation, NodeName
│   ├── sessions.py                # in-memory sessions, background pipeline, get_provider()
│   ├── models.py                  # Pydantic models (ExtractedBill, Finding, AnalysisResult, …)
│   ├── llm/provider.py            # LLMProvider protocol + Anthropic provider
│   ├── pipeline/                  # the five agents + shared state
│   │   ├── graph.py               # Extractor → Auditor → Rights_Checker → Skeptic → Writer
│   │   ├── state.py               # PipelineState, NodeStatus
│   │   ├── extractor.py           # pdfplumber text → LLM → ExtractedBill
│   │   ├── sumcheck.py            # deterministic Decimal Sum_Check
│   │   ├── auditor.py             # math / duplicate / suspicious-code rules
│   │   ├── rights_checker.py      # BM25 retrieval, 0.7 threshold, top 3
│   │   ├── skeptic.py             # confidence ≥ 0.5 + evidence filter
│   │   └── writer.py              # summary (session language), English letter, translation
│   ├── rag/
│   │   ├── knowledge_base.py      # BM25 index over the corpus
│   │   └── corpus/                # MA patient-rights passages (JSON, with titles)
│   ├── eval/                      # harness + 10-bill synthetic eval set (PDFs + manifest)
│   └── *_test.py                  # endpoint and integration tests (pipeline tests live in pipeline/)
└── frontend/                      # Next.js app
    └── src/
        ├── app/
        │   ├── layout.tsx         # sticky header + footer
        │   ├── page.tsx           # home (server: hero-image check) → HomeContent
        │   ├── components/        # SiteHeader, SiteFooter, AppShell, HomeContent, HeroVisual, UploadCard
        │   ├── progress/[sessionId]/  # live five-agent stepper (SSE)
        │   └── results/[sessionId]/   # tabs, IssueCard, LetterTranslation, LetterActions,
        │                              # ReadAloudButton + readAloud controller, VoiceQA chat
        ├── i18n/                  # dictionary.ts (4 languages), uiLanguage.ts (store)
        └── lib/api.ts             # typed backend client
```

Put new pipeline logic in `backend/pipeline/` with a sibling `*_test.py`. Put new UI strings in `frontend/src/i18n/dictionary.ts`, never inline.
