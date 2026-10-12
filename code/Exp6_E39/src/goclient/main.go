package main

import (
	"bufio"
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"os"
	"regexp"

	"k8s.io/client-go/rest"
)

var serverRe = regexp.MustCompile(`^https://127\.0\.0\.1:\d+$`)

func validateServer(s string) error {
	if !serverRe.MatchString(s) {
		return fmt.Errorf("refused server %q: must be https://127.0.0.1:<port>", s)
	}
	return nil
}

func run(a *app, in io.Reader, out io.Writer) {
	sc := bufio.NewScanner(in)
	sc.Buffer(make([]byte, 1024*1024), 8*1024*1024)
	enc := json.NewEncoder(out)
	for sc.Scan() {
		line := sc.Bytes()
		if len(line) == 0 {
			continue
		}
		var cmd command
		if err := json.Unmarshal(line, &cmd); err != nil {
			_ = enc.Encode(result{OK: false, Error: "bad json: " + err.Error()})
			continue
		}
		ctx := context.Background()
		var r result
		switch cmd.Op {
		case "ping":
			r = result{Op: "ping", OK: true}
		case "quit":
			_ = enc.Encode(result{Op: "quit", OK: true})
			return
		case "gate_uid":
			r = a.opGateUID(ctx, cmd)
		case "delete_uid":
			r = a.opDeleteUID(ctx, cmd)
		case "delete_rv":
			r = a.opDeleteRV(ctx, cmd)
		case "gate_delete_uid":
			r = a.gateDeleteUID(ctx, cmd)
		case "gate_delete_rv":
			r = a.gateDeleteRV(ctx, cmd)
		default:
			r = result{Op: cmd.Op, OK: false, Error: "unknown op: " + cmd.Op}
		}
		_ = enc.Encode(r)
	}
}

func main() {
	server := flag.String("server", "", "API server URL; must be https://127.0.0.1:<port>")
	caFile := flag.String("ca-file", "", "path to CA certificate file")
	tokenFile := flag.String("token-file", "", "path to bearer token file")
	nsPrefix := flag.String("namespace-prefix", "e39-", "required namespace prefix")
	flag.Parse()

	if err := validateServer(*server); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(2)
	}

	cfg := &rest.Config{
		Host:            *server,
		BearerTokenFile: *tokenFile,
		TLSClientConfig: rest.TLSClientConfig{CAFile: *caFile},
		QPS:             1000,
		Burst:           2000,
	}
	a, err := newApp(cfg, *nsPrefix)
	if err != nil {
		fmt.Fprintln(os.Stderr, "init:", err)
		os.Exit(1)
	}
	run(a, os.Stdin, os.Stdout)
}
