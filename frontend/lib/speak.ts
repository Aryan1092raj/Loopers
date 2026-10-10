/** Play text through the server TTS; fall back to the browser's speech synthesis if the call or playback fails. */
export async function speak(text: string): Promise<void> {
  try {
    const res = await fetch("/api/voice/tts", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ text }) });
    if (!res.ok) throw new Error(res.statusText);
    const url = URL.createObjectURL(await res.blob());
    const audio = new Audio(url);
    audio.onended = () => URL.revokeObjectURL(url);
    await audio.play();
  } catch {
    if ("speechSynthesis" in window) speechSynthesis.speak(new SpeechSynthesisUtterance(text));
  }
}
