-- yay-llm-review AURPreInstall adapter.
-- The Python helper owns configuration, HTTP, caching, and policy decisions.

local function shell_quote(value)
    return "'" .. tostring(value):gsub("'", "'\"'\"'") .. "'"
end

local function read_first_line(path)
    local file = io.open(path, "r")
    if not file then
        return nil
    end
    local line = file:read("*l")
    file:close()
    return line
end

yay.create_autocmd("AURPreInstall", {
    desc = "review AUR package files with a configured llama.cpp model",
    callback = function(event)
        local result_path = os.tmpname()
        local review_command = os.getenv("YAY_LLM_REVIEW_COMMAND") or "/usr/bin/yay-llm-review"
        local command = table.concat({
            shell_quote(review_command), "hook",
            "--package-base", shell_quote(event.match),
            "--package-dir", shell_quote(event.data.dir),
            "--result-file", shell_quote(result_path),
        }, " ")

        os.execute(command)
        local verdict = read_first_line(result_path)
        os.remove(result_path)

        if verdict == "ALLOW" then
            return
        end
        if verdict == "WARN" then
            yay.log.warn(event.match .. ": LLM review requires attention; continuing")
            return
        end
        if verdict == "BLOCK" then
            yay.abort(event.match .. ": blocked by yay-llm-review")
        end

        yay.abort(event.match .. ": yay-llm-review did not produce a valid verdict")
    end,
})
