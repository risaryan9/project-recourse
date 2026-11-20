import type { Metadata, Viewport } from "next";
import { Mona_Sans } from "next/font/google";
import "./globals.css";

// One family for everything. `latin-ext` carries the ₹ glyph; the width axis gives the
// slightly condensed headlines (see DESIGN.md).
const mona = Mona_Sans({
  variable: "--font-mona",
  subsets: ["latin", "latin-ext"],
  axes: ["wdth"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "Recourse: get back what your bank owes you",
  description:
    "When a UPI payment fails, an ATM keeps your cash or money leaves your account without you, RBI rules say what you are owed. Recourse works it out, writes the complaint and follows it through.",
};

export const viewport: Viewport = {
  themeColor: "#050507",
  colorScheme: "dark",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en-IN" className={`${mona.variable} dark h-full antialiased`}>
      <body className="min-h-full">{children}</body>
    </html>
  );
}
