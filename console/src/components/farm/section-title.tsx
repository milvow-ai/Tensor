import { CardTitle } from "@/components/ui/card";

/** A card title that is also a real heading for assistive tech (the starter's CardTitle is a plain div). */
export function SectionTitle({ level = 2, ...props }: React.ComponentProps<typeof CardTitle> & { level?: 1 | 2 | 3 }) {
  return <CardTitle role="heading" aria-level={level} {...props} />;
}
