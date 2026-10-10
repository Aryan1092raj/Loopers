import "./globals.css";
import type { Metadata } from "next";
import Nav from "@/components/Nav";

export const metadata: Metadata = { title: "Loopers Study Companion" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-neutral-50 text-neutral-900">
        <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:rounded focus:bg-white focus:px-3 focus:py-1 focus:shadow">Skip to content</a>
        <header className="border-b bg-white">
          <Nav />
        </header>
        {/* same container and gutters as the nav, so both edges line up; wide enough that content is not squeezed into a centred column */}
        <main id="main" className="mx-auto w-full max-w-[96rem] px-3 py-4 sm:px-4">{children}</main>
      </body>
    </html>
  );
}
