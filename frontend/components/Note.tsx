export function ErrorNote({ message }: { message: string | null }) {
  if (!message) return null;
  return <p role="alert" className="rounded border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-800">{message}</p>;
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <p className="rounded border border-dashed border-neutral-300 px-3 py-6 text-center text-sm text-neutral-600">{children}</p>;
}
