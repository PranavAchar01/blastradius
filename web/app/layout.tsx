import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "blastradius — supply-chain blast radius console",
  description:
    "When an npm package is compromised: who is exposed, how deep, and since when. Exact graph traversal over 25,000 packages and the complete OSV advisory corpus.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
