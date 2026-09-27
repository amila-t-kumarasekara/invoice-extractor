import DocumentDetailClient from "./DocumentDetailClient";

export default async function Page(props: PageProps<"/documents/[id]">) {
  const { id } = await props.params;
  return <DocumentDetailClient id={id} />;
}
