import { Suspense } from "react";

import { Working } from "@/components/app/ui";
import { CaseScreen } from "@/components/case/case-screen";

export default function CasePage({ params }: PageProps<"/cases/[id]">) {
  return (
    <Suspense fallback={<div className="pt-12"><Working>Loading the case</Working></div>}>
      <CaseScreen params={params} />
    </Suspense>
  );
}
