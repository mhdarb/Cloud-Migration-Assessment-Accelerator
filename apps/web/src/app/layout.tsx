import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "Cloud Migration Assessment Accelerator",
  description: "Source-linked cloud migration readiness assessments",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>
        <div className="min-h-screen">
          <header className="border-b border-[var(--border)] bg-white">
            <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-4">
              <Link href="/" className="flex items-center gap-3">
                <div className="h-3 w-10 bg-[var(--accent)]" />
                <div>
                  <div className="text-lg font-semibold tracking-tight">
                    Cloud Migration Assessment Accelerator
                  </div>
                  <div className="sans text-xs text-[var(--muted)]">
                    Source-linked migration readiness
                  </div>
                </div>
              </Link>
            </div>
          </header>
          <main className="mx-auto max-w-6xl px-6 py-8">{children}</main>
        </div>
      </body>
    </html>
  );
}
