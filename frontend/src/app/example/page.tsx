/**
 * Example analysis (instant demo).
 *
 * Renders a saved, real pipeline run on the synthetic sample bill (bill_10)
 * from `example-result.json`, using the normal results view in Example mode:
 * all tabs work and nothing calls the backend.
 *
 * Regenerate the JSON from `backend/` by running the pipeline on
 * `eval/eval_set/bill_10.pdf` and serializing with `main._serialize_result`
 * (see the README).
 */
import AppShell from "../components/AppShell";
import ResultsView, { type LoadedResult } from "../results/[sessionId]/ResultsView";
import { normalizeResult, type ResultResponse, type SupportedLanguage } from "@/lib/api";
import exampleJson from "./example-result.json";

const raw = exampleJson as ResultResponse & { language: SupportedLanguage };

const EXAMPLE_RESULT: LoadedResult = {
  ...normalizeResult(raw),
  language: raw.language,
};

export default function ExamplePage() {
  return (
    <AppShell page="example">
      <ResultsView sessionId="example" example={EXAMPLE_RESULT} />
    </AppShell>
  );
}
