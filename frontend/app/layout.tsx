import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "Clawback",
  description: "AI deductions recovery for CPG brands",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header className="topbar">
          <Link href="/" className="brand">
            Clawback
          </Link>
          <span className="tagline">Deductions recovery for consumer brands</span>
        </header>
        <main>{children}</main>
      </body>
    </html>
  );
}
