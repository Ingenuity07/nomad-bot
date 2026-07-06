import os
import json
import base64
import subprocess
from typing import Dict, Any, List
from ..base import BaseTool

def get_github_env() -> Dict[str, str]:
    """
    Builds the environment dictionary for running gh CLI,
    injecting GITHUB_TOKEN/GH_TOKEN from the project root's .env file.
    """
    env = os.environ.copy()
    
    # Locate the root .env file (4 levels up from this file)
    try:
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        env_path = os.path.join(base_dir, ".env")
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    if "=" in line and not line.strip().startswith("#"):
                        key, val = line.strip().split("=", 1)
                        if key.strip() in ("GITHUB_TOKEN", "GH_TOKEN"):
                            token = val.strip().strip("'").strip('"')
                            env["GH_TOKEN"] = token
                            env["GITHUB_TOKEN"] = token
                            break
    except Exception:
        pass
        
    return env

def run_gh_command(args: List[str]) -> tuple[int, str, str]:
    """
    Helper to run a gh command using the configured environment.
    Returns (exit_code, stdout, stderr).
    """
    env = get_github_env()
    try:
        result = subprocess.run(
            ["gh"] + args,
            capture_output=True,
            text=True,
            env=env,
            timeout=60
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "Error: Command timed out after 60 seconds."
    except Exception as e:
        return -1, "", f"Error running gh command: {str(e)}"


class GitHubSearchCodeTool(BaseTool):
    """Tool to search code in GitHub repositories."""
    
    @property
    def name(self) -> str:
        return "github_search_code"
        
    @property
    def description(self) -> str:
        return "Search for code across GitHub repositories using the GitHub CLI search API."
        
    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query term or pattern (e.g. 'Software Engineer resume')."
                },
                "repo": {
                    "type": "string",
                    "description": "Optional repository filter in format 'owner/repo' (e.g. 'Ingenuity07/resume')."
                },
                "limit": {
                    "type": "integer",
                    "description": "Optional maximum number of results to return (default 10)."
                }
            },
            "required": ["query"]
        }
        
    def execute(self, query: str, repo: str = None, limit: int = 10, **kwargs) -> str:
        args = ["search", "code", query, "--json", "path,repository,textMatches", "--limit", str(limit)]
        if repo:
            args.extend(["--repo", repo])
            
        code, out, err = run_gh_command(args)
        if code != 0:
            return f"Error searching code: {err or out}"
            
        try:
            results = json.loads(out)
            if not results:
                return "No matching code results found."
                
            formatted = [f"Found {len(results)} match(es):"]
            for idx, r in enumerate(results, 1):
                path = r.get("path", "unknown")
                repo_info = r.get("repository", {}).get("fullName", "unknown")
                formatted.append(f"\n{idx}. Repository: {repo_info} | Path: {path}")
                matches = r.get("textMatches", [])
                if matches:
                    snippet = matches[0].get("fragment", "").strip()
                    if snippet:
                        formatted.append(f"   Matches:\n   ```\n   {snippet}\n   ```")
            return "\n".join(formatted)
        except Exception as e:
            return f"Error parsing search output: {str(e)}\nRaw Output: {out}"


class GitHubReadFileTool(BaseTool):
    """Tool to read files from GitHub repositories."""
    
    @property
    def name(self) -> str:
        return "github_read_file"
        
    @property
    def description(self) -> str:
        return "Read the text content of a specific file from a GitHub repository."
        
    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "The repository name in format 'owner/repo' (e.g. 'Ingenuity07/resume')."
                },
                "path": {
                    "type": "string",
                    "description": "The path to the file within the repository (e.g. 'resume.md')."
                },
                "ref": {
                    "type": "string",
                    "description": "Optional branch, tag, or commit SHA (default is 'main')."
                }
            },
            "required": ["repo", "path"]
        }
        
    def execute(self, repo: str, path: str, ref: str = "main", **kwargs) -> str:
        args = ["api", f"repos/{repo}/contents/{path}", "-f", f"ref={ref}"]
        code, out, err = run_gh_command(args)
        if code != 0:
            return f"Error reading file: {err or out}"
            
        try:
            data = json.loads(out)
            # Handle if path is a directory instead of a file
            if isinstance(data, list):
                items = [f"{item['name']} ({item['type']})" for item in data]
                return f"Path '{path}' is a directory containing:\n" + "\n".join(items)
                
            encoding = data.get("encoding")
            content_encoded = data.get("content", "")
            
            if encoding == "base64":
                content_decoded = base64.b64decode(content_encoded).decode("utf-8")
                lines = content_decoded.splitlines()
                # Format with line numbers for reasoning context
                formatted_lines = [f"{i+1}: {line}" for i, line in enumerate(lines)]
                header = f"File: `{repo}/{path}` (ref: `{ref}`) — {len(lines)} lines\n\n"
                return header + "\n".join(formatted_lines)
            else:
                return f"Error: Unsupported file encoding: {encoding}"
        except UnicodeDecodeError:
            return f"Error: Binary or non-UTF8 file cannot be displayed as text."
        except Exception as e:
            return f"Error parsing API response: {str(e)}"


class GitHubWriteFileTool(BaseTool):
    """Tool to write/commit files to a branch in GitHub."""
    
    @property
    def name(self) -> str:
        return "github_write_file"
        
    @property
    def description(self) -> str:
        return "Create or update a file in a GitHub repository branch, committing and pushing the changes."
        
    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "The repository name in format 'owner/repo'."
                },
                "path": {
                    "type": "string",
                    "description": "The file path within the repository."
                },
                "content": {
                    "type": "string",
                    "description": "The content to write to the file."
                },
                "branch": {
                    "type": "string",
                    "description": "The branch to commit to."
                },
                "commit_message": {
                    "type": "string",
                    "description": "The commit message for this write operation."
                },
                "base_branch": {
                    "type": "string",
                    "description": "Optional base branch to branch from if target branch doesn't exist (default 'main')."
                }
            },
            "required": ["repo", "path", "content", "branch", "commit_message"]
        }
        
    def execute(self, repo: str, path: str, content: str, branch: str, commit_message: str, base_branch: str = "main", **kwargs) -> str:
        # Safety Guard 1: Direct write to protected branches
        if branch.lower() in ("main", "master", "production"):
            return f"Error: Direct commits to protected branch '{branch}' are blocked for safety reasons."
            
        # Safety Guard 2: Writing/modifying protected sensitive files
        import fnmatch
        protected_patterns = [
            ".github/workflows/*",  # CI/CD pipelines
            "*.pem", "*.key",       # Private keys
            "secrets/*",            # Secret files
            ".env*",                # Environment/config files with secrets
            "Dockerfile",           # Docker configurations
            "docker-compose*.yml"   # Compose configurations
        ]
        
        file_basename = os.path.basename(path)
        for pattern in protected_patterns:
            if fnmatch.fnmatch(path.lower(), pattern.lower()) or fnmatch.fnmatch(file_basename.lower(), pattern.lower()):
                return f"Error: Commit of protected file '{path}' is blocked for safety reasons."

        # Step 1: Check if the branch exists
        code, out, err = run_gh_command(["api", f"repos/{repo}/git/ref/heads/{branch}"])
        if code != 0:
            # Branch does not exist, let's create it from base_branch
            # First get base branch SHA
            code_base, out_base, err_base = run_gh_command(["api", f"repos/{repo}/git/ref/heads/{base_branch}"])
            if code_base != 0:
                return f"Error: Base branch '{base_branch}' does not exist: {err_base or out_base}"
            try:
                base_sha = json.loads(out_base)["object"]["sha"]
            except Exception as e:
                return f"Error extracting base branch SHA: {str(e)}"
                
            # Create branch refs
            create_payload = {"ref": f"refs/heads/{branch}", "sha": base_sha}
            code_create, out_create, err_create = run_gh_command([
                "api", f"repos/{repo}/git/refs",
                "--method", "POST",
                "--input", "-"
            ])
            # Pass payload to stdin using custom wrapper or env config, but wait, gh api has standard -f / -F parameters.
            # Let's do it via gh api with parameters to be absolutely safe
            create_args = [
                "api", f"repos/{repo}/git/refs",
                "--method", "POST",
                "-f", f"ref=refs/heads/{branch}",
                "-f", f"sha={base_sha}"
            ]
            code_create, out_create, err_create = run_gh_command(create_args)
            if code_create != 0:
                return f"Error creating branch '{branch}': {err_create or out_create}"
                
        # Step 2: Get existing file SHA if it exists on target branch (needed for update)
        code_file, out_file, err_file = run_gh_command(["api", f"repos/{repo}/contents/{path}", "-f", f"ref={branch}"])
        file_sha = ""
        if code_file == 0:
            try:
                file_sha = json.loads(out_file).get("sha", "")
            except Exception:
                pass
                
        # Step 3: Write file content via Contents API
        content_b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
        write_args = [
            "api", f"repos/{repo}/contents/{path}",
            "--method", "PUT",
            "-f", f"message={commit_message}",
            "-f", f"content={content_b64}",
            "-f", f"branch={branch}"
        ]
        if file_sha:
            write_args.extend(["-f", f"sha={file_sha}"])
            
        code_write, out_write, err_write = run_gh_command(write_args)
        if code_write != 0:
            return f"Error committing file changes: {err_write or out_write}"
            
        try:
            resp_data = json.loads(out_write)
            html_url = resp_data.get("content", {}).get("html_url", "")
            return f"Success: File successfully committed to branch '{branch}'. Link: {html_url}"
        except Exception:
            return "Success: File successfully committed."


class GitHubCreatePRTool(BaseTool):
    """Tool to create Pull Requests."""
    
    @property
    def name(self) -> str:
        return "github_create_pr"
        
    @property
    def description(self) -> str:
        return "Create a Pull Request in a GitHub repository from a head branch into a base branch."
        
    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "The repository name in format 'owner/repo'."
                },
                "title": {
                    "type": "string",
                    "description": "The title of the Pull Request."
                },
                "body": {
                    "type": "string",
                    "description": "The description/body content of the Pull Request."
                },
                "head": {
                    "type": "string",
                    "description": "The branch containing changes to merge."
                },
                "base": {
                    "type": "string",
                    "description": "Optional base branch to merge into (default is 'main')."
                }
            },
            "required": ["repo", "title", "body", "head"]
        }
        
    def execute(self, repo: str, title: str, body: str, head: str, base: str = "main", **kwargs) -> str:
        args = [
            "pr", "create",
            "--repo", repo,
            "--head", head,
            "--base", base,
            "--title", title,
            "--body", body
        ]
        code, out, err = run_gh_command(args)
        if code != 0:
            return f"Error creating Pull Request: {err or out}"
            
        return f"Success: Pull Request created. Link: {out.strip()}"
