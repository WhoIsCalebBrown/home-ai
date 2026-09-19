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

const FINAL_ANSWER = "Here is the fixture answer.";
const TRACE_TEXT = "Research activity";
const SAFE_URL = "https://example.com/news";
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
  assert(snapshot.injected_node_count === 0, phase + ": hostile title created injected node");
  assert(snapshot.text.includes("spoof"), phase + ": hostile title text was not visible");
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
  fs.mkdirSync(artifactsDir, { recursive: true });

  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  page.setDefaultTimeout(15000);
  const result = { base_url: baseUrl, fixture_delay_ms: 3000 };
  try {
    await authenticate(page, baseUrl, email, password);
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
