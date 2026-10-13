import "./globals.css";
import type { Metadata } from "next";
import Nav from "@/components/Nav";
import Ripple from "@/components/Ripple";

export const metadata: Metadata = { title: "Loopers Study Companion" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen text-slate-900">
        <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-white focus:px-3 focus:py-1 focus:shadow">Skip to content</a>
        <header className="sticky top-0 z-20 border-b border-white/60 bg-white/55 shadow-[0_8px_24px_-18px_rgb(60_60_140/0.4)] backdrop-blur-md">
          <Nav />
        </header>
        {/* same container and gutters as the nav, so both edges line up; wide enough that content is not squeezed into a centred column */}
        <main id="main" className="mx-auto w-full max-w-[96rem] px-3 py-5 sm:px-5">{children}</main>
        <Ripple />
      </body>
    </html>
  );
}
