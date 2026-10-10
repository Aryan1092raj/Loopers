import "./globals.css";
import type { Metadata } from "next";
import Nav from "@/components/Nav";

export const metadata: Metadata = { title: "Loopers Study Companion" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-neutral-50 text-neutral-900">
        <Nav />
        <main className="mx-auto max-w-7xl p-4">{children}</main>
      </body>
    </html>
  );
}
