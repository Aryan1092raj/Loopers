import "./globals.css";
import type { Metadata } from "next";
import Nav from "@/components/Nav";
import Ripple from "@/components/Ripple";
import ScienceBackground from "@/components/ScienceBackground";
import TargetCursor from "@/components/TargetCursor";

export const metadata: Metadata = { title: "Loopers Study Companion" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen text-slate-900">
        <ScienceBackground />
        {/* Mouse + fine pointer only (the component checks itself). Violet-indigo to match the "∞" logo
            mark (Nav.tsx's from-indigo-500 to-violet-500 badge). The dot is a hollow ring (see globals.css)
            so it never sits as a solid block over whatever it's hovering, and the four corner brackets —
            restored, see TargetCursor.jsx — only snap into view once locked onto an actual link or button,
            not as an idle spin around the bare dot. */}
        <TargetCursor
          targetSelector="a[href], button:not(:disabled), [role='button']:not([aria-disabled='true'])"
          cursorColor="#7d66e9"
          cursorColorOnTarget="#9d5cf6"
          blendMode="normal"
          dotSize={18}
          size={40}
          container={undefined}
        />
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
