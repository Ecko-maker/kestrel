// Render chat prompts exactly as the Ollama server does, for the template parity check.
//
// Input (stdin): {"template": "<Go template text>", "cases": [{"name": ..., "request": <an OpenAI
// chat-completions request: model, messages, tools>}]}. Output (stdout): {"<name>": "<rendered prompt>"}.
//
// Each request goes through the same two steps as a request from Kestrel's Ollama provider (which uses
// the OpenAI-compatible endpoint): openai.FromChatRequest (OpenAI format -> Ollama messages and tools,
// e.g. argument strings -> maps), then template.Parse + Execute (server/prompt.go renderPrompt). The
// Ollama version is pinned in go.mod to the one installed; bump both together.
package main

import (
	"encoding/json"
	"fmt"
	"os"
	"strings"

	"github.com/ollama/ollama/openai"
	"github.com/ollama/ollama/template"
)

type input struct {
	Template string `json:"template"`
	Cases    []struct {
		Name    string                       `json:"name"`
		Request openai.ChatCompletionRequest `json:"request"`
	} `json:"cases"`
}

func fail(format string, args ...any) {
	fmt.Fprintf(os.Stderr, format+"\n", args...)
	os.Exit(1)
}

func main() {
	var in input
	if err := json.NewDecoder(os.Stdin).Decode(&in); err != nil {
		fail("reading input: %v", err)
	}
	tmpl, err := template.Parse(in.Template)
	if err != nil {
		fail("parsing template: %v", err)
	}
	out := map[string]string{}
	for _, c := range in.Cases {
		req, err := openai.FromChatRequest(c.Request)
		if err != nil {
			fail("%s: converting request: %v", c.Name, err)
		}
		var b strings.Builder
		if err := tmpl.Execute(&b, template.Values{Messages: req.Messages, Tools: req.Tools}); err != nil {
			fail("%s: rendering: %v", c.Name, err)
		}
		out[c.Name] = b.String()
	}
	enc := json.NewEncoder(os.Stdout)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(out); err != nil {
		fail("writing output: %v", err)
	}
}
