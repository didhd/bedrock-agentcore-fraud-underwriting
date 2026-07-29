import defaultMdxComponents from 'fumadocs-ui/mdx';
import { Tab, Tabs } from 'fumadocs-ui/components/tabs';
import { Accordion, Accordions } from 'fumadocs-ui/components/accordion';
import { Step, Steps } from 'fumadocs-ui/components/steps';
import { File, Files, Folder } from 'fumadocs-ui/components/files';
import { TypeTable } from 'fumadocs-ui/components/type-table';
import { Callout } from 'fumadocs-ui/components/callout';
import { Card, Cards } from 'fumadocs-ui/components/card';
import { Mermaid } from '@/components/mermaid';
import type { MDXComponents } from 'mdx/types';

/**
 * Components available in every MDX page without an import.
 *
 * Tabs/Tab, Steps/Step, Accordions/Accordion, Files and TypeTable are NOT part of
 * fumadocs' default MDX components — a page using them fails at prerender with
 * "Expected component `Tab` to be defined". They are registered here so any page in
 * content/docs can use them.
 */
export function getMDXComponents(components?: MDXComponents) {
  return {
    ...defaultMdxComponents,
    Callout,
    Card,
    Cards,
    Tabs,
    Tab,
    Accordions,
    Accordion,
    Steps,
    Step,
    Files,
    File,
    Folder,
    TypeTable,
    Mermaid,
    ...components,
  } satisfies MDXComponents;
}

export const useMDXComponents = getMDXComponents;

declare global {
  type MDXProvidedComponents = ReturnType<typeof getMDXComponents>;
}
