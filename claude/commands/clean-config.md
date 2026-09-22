---
description: Create or explain .clean-code.json for this project
allowed-tools: Read, Write
---
If `.clean-code.json` is missing at the project root, create it:
{"ignore":["**/node_modules/**","**/dist/**","**/*.d.ts"],"allowConsole":["**/scripts/**","**/cli/**"],"allowTodo":false,"disableRules":[],"maxCommentRatio":0.25}
Otherwise summarize what it relaxes in two lines. $ARGUMENTS
