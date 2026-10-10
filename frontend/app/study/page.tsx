import { redirect } from "next/navigation";

// The workspace at / replaced this page; keeps old links (including ?cite=) working.
export default async function StudyPage({ searchParams }: { searchParams: Promise<Record<string, string>> }) {
  const q = new URLSearchParams(await searchParams).toString();
  redirect(q ? `/?${q}` : "/");
}
