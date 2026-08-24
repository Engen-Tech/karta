export interface EnvelopePrompter {
  prompt(message: string): Promise<unknown>;
  getLastAssistantText(): string | undefined;
}

// A child agent that has finished its work sometimes ends on a prose summary
// instead of the required JSON envelope. One corrective turn recovers the work
// rather than discarding it; a second failure falls through to the caller's
// strict parse, which reports a diagnostic snippet.
export async function promptForJsonEnvelope(
  session: EnvelopePrompter,
  userPrompt: string,
  isValid: (text: string) => boolean,
  repairPrompt: string,
): Promise<string> {
  await session.prompt(userPrompt);
  let text = session.getLastAssistantText() ?? "";
  if (!isValid(text)) {
    await session.prompt(repairPrompt);
    text = session.getLastAssistantText() ?? text;
  }
  return text;
}
