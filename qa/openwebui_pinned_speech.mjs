// Execute the pinned client's actual source-map functions, with only TypeScript
// annotations erased. No locally reimplemented sanitizer/splitter is used by QA.
export const PINNED_UTILS_MAP = "/_app/immutable/chunks/BzfgYq-h.js.map";

export function pinnedSpeechProcessor(sourceMap) {
  const index = sourceMap.sources.findIndex(name => name.endsWith("src/lib/utils/index.ts"));
  const source = sourceMap.sourcesContent[index];
  if (!source) throw new Error("Pinned Open WebUI speech source is missing");
  const names = ["removeEmojis", "removeFormattings", "cleanText", "extractSentences",
    "extractParagraphsForAudio", "extractSentencesForAudio", "getMessageContentParts"];
  const functions = names.map(name => {
    const start = source.indexOf("export const " + name + " =");
    const end = source.indexOf("\n};", start);
    if (start < 0 || end < 0) throw new Error("Missing pinned function: " + name);
    return source.slice(start, end + 3).replace("export const", "const")
      .replace(/: string\[\]/g, "").replace(/: string/g, "").replace(/ as string\[\]/g, "");
  });
  return new Function(
    "const TTS_RESPONSE_SPLIT = {PUNCTUATION:'punctuation',PARAGRAPHS:'paragraphs',NONE:'none'};" +
    "const codeBlockRegex = /```[\\s\\S]*?```/g;\n" + functions.join("\n") +
    "\nreturn getMessageContentParts;"
  )();
}
