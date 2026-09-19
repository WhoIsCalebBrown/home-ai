#!/usr/bin/env node
/**
 * Browser acceptance for the unchanged disposable Open WebUI client.
 *
 * It creates/logs into a fixture account through the UI, sends the fixture
 * prompt through the contenteditable composer, observes the streaming DOM, and
 * reloads the UI to inspect Open WebUI's own persisted message. It never calls
 * a chat persistence endpoint.
 */
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { chromium } from "playwright";
import { PINNED_UTILS_MAP, pinnedSpeechProcessor } from "./openwebui_pinned_speech.mjs";

const FINAL_ANSWER = "Here is the fixture answer.";
const TRACE_TEXT = "Research activity";
const SAFE_URL = "https://example.com/news";
const HOSTILE_TITLE_WITNESS = "Unsafe title witness";
const PROMPT = "Show the QA fixture for household-private-query.";
const SAFE_PROGRESS = ["Searching the web…", "Reading example.com…"];
const FORBIDDEN = [
  "household-private-query", "private-source.invalid", "raw fixture snippet",
  "fixture-token-9f1c", "tool exception fixture", "traceback", "127.0.0.1",
  "localhost",
];

function option(name, fallback) {
  const index = process.argv.indexOf(name);
  return index >= 0 ? process.argv[index + 1] : fallback;
}

function requiredOption(name) {
  const value = option(name);
  if (!value || value.startsWith("--")) throw new Error(name + " is required");
  return value;
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

async function dismissReleaseNotes(page) {
  const notes = page.locator("button").filter({ hasText: "Okay" });
  if (await notes.count()) await notes.first().click({ force: true, noWaitAfter: true });
}

async function authenticate(page, baseUrl, email, password) {
  await page.goto(baseUrl.replace(/\/$/, "") + "/auth?redirect=%2F", { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(300);
  if (await page.getByRole("button", { name: "Get started" }).count()) {
    await page.getByRole("button", { name: "Get started" }).click();
    await page.locator("input").nth(0).fill("Progress Source QA");
    await page.locator("input[type=email]").fill(email);
    await page.locator("input[type=password]").fill(password);
    await page.getByRole("button", { name: "Create Admin Account" }).click({ force: true, noWaitAfter: true });
  } else {
    await page.locator("input[type=email]").fill(email);
    await page.locator("input[type=password]").fill(password);
    await page.getByRole("button", { name: "Sign in" }).click({ force: true, noWaitAfter: true });
  }
  await page.waitForTimeout(1600);
  await dismissReleaseNotes(page);
  await page.locator("button[aria-label*=Selected]").waitFor();
}

async function completedAssistantDom(page) {
  return page.evaluate(({ finalAnswer, safeUrl }) => {
    const safeSelector = 'a[href="' + safeUrl + '"]';
    const candidates = [...document.querySelectorAll("div")]
      .filter(node => node.innerText?.includes(finalAnswer)
        && node.querySelector(safeSelector))
      .sort((left, right) => left.innerText.length - right.innerText.length);
    const root = candidates[0];
    if (!root) return null;
    const link = root.querySelector(safeSelector);
    return {
      text: root.innerText,
      separator_count: root.querySelectorAll("hr").length,
      safe_anchor_count: root.querySelectorAll(safeSelector).length,
      safe_anchor_clickable: Boolean(link)
        && !link.hasAttribute("disabled")
        && getComputedStyle(link).pointerEvents !== "none",
      evil_anchor_count: root.querySelectorAll('a[href*="evil.example"]').length,
      all_anchors: [...root.querySelectorAll("a[href]")].map(node => node.href),
      injected_node_count: root.querySelectorAll('img[src="x"], script, [onerror], [onload]').length,
    };
  }, { finalAnswer: FINAL_ANSWER, safeUrl: SAFE_URL });
}

function checkCompletedDom(snapshot, phase) {
  assert(snapshot, phase + ": could not locate completed assistant message");
  const answerIndex = snapshot.text.indexOf(FINAL_ANSWER);
  const traceIndex = snapshot.text.indexOf(TRACE_TEXT);
  assert(answerIndex >= 0, phase + ": missing final answer");
  assert(traceIndex > answerIndex, phase + ": answer must precede trace");
  assert(snapshot.separator_count >= 1, phase + ": missing rendered separator");
  assert(snapshot.safe_anchor_count === 1, phase + ": safe source anchor missing or duplicated");
  assert(snapshot.safe_anchor_clickable, phase + ": safe source anchor is not clickable");
  assert(snapshot.evil_anchor_count === 0, phase + ": hostile title created evil destination");
  assert(snapshot.all_anchors.every(url => url === SAFE_URL), phase + ": non-public source anchor");
  assert(snapshot.injected_node_count === 0, phase + ": hostile title created injected node");
  assert(snapshot.text.includes(HOSTILE_TITLE_WITNESS),
    phase + ": sanitized hostile-title witness was not visible as inert text");
  assert(!FORBIDDEN.some(value => snapshot.text.includes(value)), phase + ": raw fixture data leaked");
}

function progressLineCount(text) {
  return SAFE_PROGRESS.filter(label => text.includes(label)).length;
}

async function main() {
  const baseUrl = option("--base-url", process.env.OPENWEBUI_BASE_URL || "http://home-ai-progress-source-webui:8080");
  const artifactsDir = requiredOption("--artifacts-dir");
  const email = option("--email", "progress-source@example.test");
  const password = option("--password", "FixturePassword123!");
  const providerUrl = option("--provider-url", "http://progress-source-provider:8000");
  fs.mkdirSync(artifactsDir, { recursive: true });

  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  page.setDefaultTimeout(15000);
  const result = { base_url: baseUrl, fixture_delay_ms: 3000 };
  try {
    await authenticate(page, baseUrl, email, password);
    const headers = { Authorization: "Bearer progress-source-fixture-key", "Content-Type": "application/json" };
    const sourceMap = await (await fetch(baseUrl + PINNED_UTILS_MAP)).json();
    const getMessageContentParts = pinnedSpeechProcessor(sourceMap);
    const input = page.locator("[contenteditable=true]");
    await input.fill(PROMPT);
    const started = performance.now();
    await input.press("Control+Enter");
    await page.waitForTimeout(400);
    const earlyText = await page.locator("body").innerText();
    result.early_progress_visible = SAFE_PROGRESS.every(label => earlyText.includes(label));
    result.early_final_visible = earlyText.includes(FINAL_ANSWER);
    result.early_elapsed_ms = Math.round(performance.now() - started);
    assert(result.early_progress_visible, "early: ordinary Working progress was not visible");
    assert(!result.early_final_visible, "early: final answer arrived before blocked source completed");
    assert(result.early_elapsed_ms < result.fixture_delay_ms, "early: observation was after fixture release");
    await page.screenshot({ path: path.join(artifactsDir, "progress-visible-before-source-completes.png"), fullPage: true });
    const live = await (await fetch(providerUrl + "/qa/evidence", { headers })).json();
    result.live_speech_silent_count = 0;
    for (const mode of ["punctuation", "paragraphs", "none"]) {
      for (const input of getMessageContentParts(live.progress, mode)) {
        const response = await fetch(providerUrl + "/v1/audio/speech", {
          method: "POST", headers, body: JSON.stringify({ input }),
        });
        assert(response.status === 204, mode + ": live progress was spoken before the answer existed");
        result.live_speech_silent_count++;
      }
    }

    await page.getByText(FINAL_ANSWER, { exact: true }).waitFor();
    const completed = await completedAssistantDom(page);
    checkCompletedDom(completed, "completed");
    result.completed_progress_line_count = progressLineCount(completed.text);
    assert(result.completed_progress_line_count >= 1 && result.completed_progress_line_count <= 4,
      "completed: persisted preamble must contain one through four safe lines");
    result.final_elapsed_ms = Math.round(performance.now() - started);
    await page.screenshot({ path: path.join(artifactsDir, "progress-source-final.png"), fullPage: true });

    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForTimeout(750);
    const reloaded = await completedAssistantDom(page);
    checkCompletedDom(reloaded, "reload");
    result.reloaded_progress_line_count = progressLineCount(reloaded.text);
    assert(result.reloaded_progress_line_count >= 1 && result.reloaded_progress_line_count <= 4,
      "reload: persisted preamble must contain one through four safe lines");
    await page.screenshot({ path: path.join(artifactsDir, "progress-source-reload.png"), fullPage: true });
    const evidence = await (await fetch(providerUrl + "/qa/evidence", { headers })).json();
    result.speech = {};
    let expectedSyntheses = evidence.spoken.length;
    for (const mode of ["punctuation", "paragraphs", "none"]) {
      let silent = 0, spoken = 0;
      for (const value of [evidence.display, evidence.progress, evidence.footer]) {
        for (const input of getMessageContentParts(value, mode)) {
          const expected = input.includes(FINAL_ANSWER) ? FINAL_ANSWER : "";
          const response = await fetch(providerUrl + "/v1/audio/speech", {
            method: "POST", headers, body: JSON.stringify({ input, response_format: "wav" }),
          });
          assert(response.status === (expected ? 200 : 204), mode + ": wrong speech status for " + input);
          assert(await response.text() === expected, mode + ": display metadata reached synthesis");
          expected ? spoken++ : silent++;
        }
      }
      expectedSyntheses += spoken;
      result.speech[mode] = { silent, spoken };
    }
    const after = await (await fetch(providerUrl + "/qa/evidence", { headers })).json();
    assert(after.spoken.length === expectedSyntheses && after.spoken.every(text => text === FINAL_ANSWER),
      "Real speech handler invoked the synthesizer for metadata");

    const native = fs.readFileSync(path.resolve(path.dirname(new URL(import.meta.url).pathname),
      "../assistant/voice-api-index.html"), "utf8");
    const nativeFunctions = ["approvedTraceUrl", "renderTrace"].map(name =>
      native.split("\n").find(line => line.startsWith("function " + name + "("))).join("\n");
    result.native_anchors = await page.evaluate(({ source, trace }) => {
      const render = new Function("const MAX_TRACE_ENTRIES=12,MAX_SOURCES_PER_SEARCH=3;\n" + source + "\nreturn renderTrace;")();
      return [...render(trace).querySelectorAll("a")].map(node => node.href);
    }, { source: nativeFunctions, trace: evidence.trace });
    assert(JSON.stringify(result.native_anchors) === JSON.stringify([SAFE_URL]), "Native trace created an unsafe anchor");
    result.status = "pass";
    fs.writeFileSync(path.join(artifactsDir, "results.json"), JSON.stringify(result, null, 2) + "\n");
    console.log(JSON.stringify(result));
  } finally {
    await browser.close();
  }
}

main().catch(error => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
