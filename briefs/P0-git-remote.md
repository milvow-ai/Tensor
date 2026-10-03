# Brief P0-git-remote — point this repo at the milvow-ai GitHub account and push

Rules: run ONLY the commands listed below, in order, in the repo root `D:\Harness Farm\Tensor`. Do not edit, create or delete any file. Do not run any other git command (no commit, reset, config of other keys, credential deletion). Do not read `.env`. The user explicitly authorised the push in step 2 on 2026-10-04.

## Steps
1. `git remote set-url origin https://milvow-ai@github.com/milvow-ai/Tensor.git`
2. `git push -u origin claude/awesome-archimedes-vb1t7y`
   - A GitHub sign-in window may open for the user; wait for the command to finish (allow up to 170 seconds). Do not retry more than once.
3. `git remote -v`
4. `git status -sb`

## Done when
Step 2 prints a line containing `claude/awesome-archimedes-vb1t7y -> claude/awesome-archimedes-vb1t7y` or `Everything up-to-date`.

## Reply (≤ 8 lines)
The exact last 3 lines of output of step 2, the output of step 3, the first line of step 4, and PASS or FAIL.
