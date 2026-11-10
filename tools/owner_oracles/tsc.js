// The TypeScript oracle for tools/measure_owner_members.py (#675).
//
//   node tools/owner_oracles/tsc.js path/to/typescript/lib/typescript.js FILES.txt
//
// FILES.txt lists one .ts/.tsx path per line. Prints, as the TypeScript
// compiler's own parser reads each file, one `file<TAB>owner<TAB>member,member`
// row per type it declares and `file<TAB><TAB>` for a file that declares none.
// The members are what `mnemo land` reads for the same owner: an interface's
// and an object type's properties, methods and accessors; a `type` alias's,
// summed over every object literal in it that sits inside no other (an alias
// with none is no owner); a class's fields, methods, accessors and parameter
// properties; an enum's members. A computed or quoted name is no identifier
// a contract could spell, and is left out.
const ts = require(process.argv[2]);
const fs = require("fs");

const files = fs.readFileSync(process.argv[3], "utf8").split(/\r?\n/).filter(Boolean);
const nameOf = (n) => n && (ts.isIdentifier(n) ? n.text : ts.isPrivateIdentifier(n) ? n.text.slice(1) : null);
const PARAMETER_PROPERTY = new Set([ts.SyntaxKind.PublicKeyword, ts.SyntaxKind.PrivateKeyword,
  ts.SyntaxKind.ProtectedKeyword, ts.SyntaxKind.ReadonlyKeyword, ts.SyntaxKind.OverrideKeyword]);

const literalMembers = (members) => members
  .filter((m) => ts.isPropertySignature(m) || ts.isMethodSignature(m) || ts.isGetAccessor(m) || ts.isSetAccessor(m))
  .map((m) => nameOf(m.name));

const classMembers = (node) => {
  const names = [];
  for (const m of node.members) {
    if (ts.isPropertyDeclaration(m) || ts.isMethodDeclaration(m) || ts.isGetAccessor(m) || ts.isSetAccessor(m)) {
      names.push(nameOf(m.name));
    } else if (ts.isConstructorDeclaration(m)) {
      for (const p of m.parameters) {
        if ((ts.getModifiers(p) || []).some((x) => PARAMETER_PROPERTY.has(x.kind))) names.push(nameOf(p.name));
      }
    }
  }
  return names;
};

for (const file of files) {
  const kind = /\.tsx$/i.test(file) ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
  const source = ts.createSourceFile(file, fs.readFileSync(file, "utf8"), ts.ScriptTarget.Latest, true, kind);
  const owners = new Map();
  const add = (owner, names) => {
    if (!owners.has(owner)) owners.set(owner, new Set());
    for (const n of names) if (n) owners.get(owner).add(n);
  };
  const visit = (node) => {
    if (ts.isInterfaceDeclaration(node)) {
      add(node.name.text, literalMembers(node.members));
    } else if (ts.isTypeAliasDeclaration(node)) {
      const names = [];
      let literal = false;
      const walk = (t) => {
        if (ts.isTypeLiteralNode(t)) { literal = true; names.push(...literalMembers(t.members)); return; }
        if (ts.isMappedTypeNode(t)) { literal = true; return; }
        ts.forEachChild(t, walk);
      };
      walk(node.type);
      if (literal) add(node.name.text, names);
    } else if ((ts.isClassDeclaration(node) || ts.isClassExpression(node)) && node.name) {
      add(node.name.text, classMembers(node));
    } else if (ts.isEnumDeclaration(node)) {
      add(node.name.text, node.members.map((m) => nameOf(m.name)));
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
  if (!owners.size) console.log(`${file}\t\t`);
  for (const [owner, names] of owners) console.log(`${file}\t${owner}\t${[...names].sort().join(",")}`);
}
