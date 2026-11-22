import { Suspense } from "react";

import { Working } from "@/components/app/ui";
import { CaseShell } from "@/components/case/case-shell";

export default function CaseLayout({ params, children }: LayoutProps<"/cases/[id]">) {
  return (
    <Suspense
      fallback={
        <div className="pt-12">
          <Working>Loading the case</Working>
        </div>
      }
    >
      <CaseShell params={params}>{children}</CaseShell>
    </Suspense>
  );
}
