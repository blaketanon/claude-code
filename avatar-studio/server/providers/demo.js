// Offline stand-ins so the full pipeline can be exercised without API keys (DEMO_MODE=1 or missing keys).
export function demoTranscript(name) {
  return `[Demo mode - no speech-to-text key configured, so this is placeholder text instead of ${name}'s real words.] Hey, I'm ${name}. Honestly I just love talking about ideas, good food, and whatever I'm building at the moment.`;
}

export function demoPersona(name) {
  return {
    summary: `${name} (demo persona - configure ANTHROPIC_API_KEY to generate a real one from the transcript).`,
    traits: ["curious", "friendly", "direct"],
    speakingStyle: "Casual, short sentences, upbeat.",
    catchphrases: [],
    vocabulary: "Everyday language.",
    topics: ["ideas", "food", "side projects"],
    values: [],
    humor: "Light and self-deprecating.",
    knownFacts: [],
    exampleLines: [`Hey, I'm ${name}.`],
    greeting: `Hey! It's ${name}. What's on your mind?`,
  };
}

export async function demoReply({ name, history, onText }) {
  const last = history.filter((m) => m.role === "user").pop()?.content ?? "";
  const reply = `(Demo mode) You said: "${last.slice(0, 200)}". Once a Claude API key is configured, I'll answer as ${name}.`;
  for (const word of reply.split(/(\s+)/)) {
    onText?.(word);
    await new Promise((r) => setTimeout(r, 15));
  }
  return reply;
}
