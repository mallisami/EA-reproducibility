package main

import (
	"context"
	"fmt"
	"net/http"
	"strings"
	"sync"
	"time"

	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/rest"

	"golang.org/x/sys/unix"
)

// ---- monotonic timing ----
//
// monoNs reads CLOCK_MONOTONIC directly, so the values are on the SAME clock
// and origin as the Python runner's time.monotonic_ns() (CPython uses
// clock_gettime(CLOCK_MONOTONIC) on Linux). Timestamps from the two clients are
// therefore directly comparable when both run on the same host.

func monoNs() int64 {
	var ts unix.Timespec
	if err := unix.ClockGettime(unix.CLOCK_MONOTONIC, &ts); err != nil {
		return time.Now().UnixNano() // fallback; not directly comparable
	}
	return ts.Nano()
}

// ---- Audit-Id capturing RoundTripper ----

type auditCapturer struct {
	rt     http.RoundTripper
	mu     sync.Mutex
	audits map[string]string // "METHOD path" -> Audit-Id
}

func (a *auditCapturer) RoundTrip(req *http.Request) (*http.Response, error) {
	resp, err := a.rt.RoundTrip(req)
	if resp != nil {
		if aid := resp.Header.Get("Audit-Id"); aid != "" {
			a.mu.Lock()
			a.audits[req.Method+" "+req.URL.Path] = aid
			a.mu.Unlock()
		}
	}
	return resp, err
}

func (a *auditCapturer) lookup(method, path string) string {
	a.mu.Lock()
	defer a.mu.Unlock()
	return a.audits[method+" "+path]
}

// ---- application state ----

type app struct {
	cs       *kubernetes.Clientset
	capturer *auditCapturer
	nsPrefix string
}

func newApp(cfg *rest.Config, nsPrefix string) (*app, error) {
	cap := &auditCapturer{audits: map[string]string{}}
	cfg.WrapTransport = func(rt http.RoundTripper) http.RoundTripper {
		cap.rt = rt
		return cap
	}
	// Force JSON so the fake test server and real server agree, and avoid protobuf.
	cfg.ContentType = "application/json"
	cfg.AcceptContentTypes = "application/json"
	if cfg.QPS == 0 {
		cfg.QPS = 1000
	}
	if cfg.Burst == 0 {
		cfg.Burst = 2000
	}
	cs, err := kubernetes.NewForConfig(cfg)
	if err != nil {
		return nil, err
	}
	return &app{cs: cs, capturer: cap, nsPrefix: nsPrefix}, nil
}

// ---- wire types ----

type command struct {
	Op              string          `json:"op"`
	Namespace       string          `json:"namespace"`
	Pod             string          `json:"pod"`
	ConfigMaps      []string        `json:"configmaps"`
	ExpectedUID     string          `json:"expected_uid"`
	ResourceVersion string          `json:"resource_version"`
	Cached          map[string]bool `json:"cached"`
}

type readInfo struct {
	ResourceVersion string `json:"resourceVersion"`
	Value           string `json:"value,omitempty"`
	Allowed         *bool  `json:"allowed,omitempty"`
	// t_send / t_recv are CLOCK_MONOTONIC ns (comparable to Python time.monotonic_ns()).
	TSend   int64  `json:"t_send"`
	TRecv   int64  `json:"t_recv"`
	AuditID string `json:"audit_id,omitempty"`
}

type result struct {
	Op                     string              `json:"op"`
	OK                     bool                `json:"ok"`
	Error                  string              `json:"error,omitempty"`
	Reads                  map[string]readInfo `json:"reads,omitempty"`
	PodUID                 string              `json:"pod_uid,omitempty"`
	PodResourceVersion     string              `json:"pod_resource_version,omitempty"`
	Valid                  bool                `json:"valid"`
	GateDecision           string              `json:"gate_decision,omitempty"`
	GateReason             string              `json:"gate_reason,omitempty"`
	TGateEndMono           int64               `json:"t_gate_end_mono_ns,omitempty"`
	Deleted                bool                `json:"deleted"`
	DeleteStatus           int                 `json:"delete_status,omitempty"`
	DeleteReason           string              `json:"delete_reason,omitempty"`
	DeleteMessage          string              `json:"delete_message,omitempty"`
	DeleteAuditID          string              `json:"delete_audit_id,omitempty"`
	DeletedResourceVersion string              `json:"deleted_resource_version,omitempty"`
	TSendMono              int64               `json:"t_send_mono_ns,omitempty"`
	TRespMono              int64               `json:"t_resp_mono_ns,omitempty"`
}

func cmPath(ns, name string) string  { return "/api/v1/namespaces/" + ns + "/configmaps/" + name }
func podPath(ns, name string) string { return "/api/v1/namespaces/" + ns + "/pods/" + name }

// strictBool matches webhook/policy.py parse_bool: only the exact strings
// "true"/"false" parse; anything else -> nil (fail closed).
func strictBool(s string) *bool {
	switch s {
	case "true":
		v := true
		return &v
	case "false":
		v := false
		return &v
	default:
		return nil
	}
}

// cmValueKey mirrors policy.VALUE_KEY: evidence-* carry the value under
// data.value; response-scope / application-policy under data.allowed.
func cmValueKey(name string) string {
	switch name {
	case "response-scope", "application-policy":
		return "allowed"
	default:
		return "value"
	}
}

// cmPred maps a ConfigMap name to its policy predicate key.
func cmPred(name string) string {
	switch name {
	case "evidence-a":
		return "a"
	case "evidence-c":
		return "c"
	case "evidence-e":
		return "e"
	case "response-scope":
		return "scope"
	case "application-policy":
		return "app"
	}
	return ""
}

func decisionStr(valid bool) string {
	if valid {
		return "delete"
	}
	return "hold"
}

func (a *app) nsOK(ns string, r *result) bool {
	if !strings.HasPrefix(ns, a.nsPrefix) {
		r.Error = fmt.Sprintf("refused namespace %q: must start with %q", ns, a.nsPrefix)
		return false
	}
	return true
}

// ---- reads ----

func (a *app) getConfigMap(ctx context.Context, ns, name string) (readInfo, *bool, error) {
	ts := monoNs()
	cm, err := a.cs.CoreV1().ConfigMaps(ns).Get(ctx, name, metav1.GetOptions{})
	tr := monoNs()
	if err != nil {
		return readInfo{TSend: ts, TRecv: tr}, nil, err
	}
	raw := cm.Data[cmValueKey(name)]
	b := strictBool(raw)
	return readInfo{
		ResourceVersion: cm.ResourceVersion,
		Value:           raw,
		Allowed:         b,
		TSend:           ts,
		TRecv:           tr,
		AuditID:         a.capturer.lookup("GET", cmPath(ns, name)),
	}, b, nil
}

func (a *app) getPod(ctx context.Context, ns, name string) (readInfo, *corev1.Pod, error) {
	ts := monoNs()
	pod, err := a.cs.CoreV1().Pods(ns).Get(ctx, name, metav1.GetOptions{})
	tr := monoNs()
	if err != nil {
		return readInfo{TSend: ts, TRecv: tr}, nil, err
	}
	return readInfo{
		ResourceVersion: pod.ResourceVersion,
		TSend:           ts,
		TRecv:           tr,
		AuditID:         a.capturer.lookup("GET", podPath(ns, name)),
	}, pod, nil
}

// ---- policy (mirrors webhook/policy.py and src/arms_commit.py exactly) ----

// decideConfigMaps evaluates the ConfigMap arm. Predicates a/c/e/scope/app come
// from ConfigMap reads (nil => missing/unparseable => fail closed); b/d/f are
// the unread cached predicates supplied in the command.
func decideConfigMaps(preds map[string]*bool, cached map[string]bool) (bool, string) {
	bad := []string{}
	for _, k := range []string{"a", "c", "e", "scope", "app"} {
		if preds[k] == nil {
			bad = append(bad, k)
		}
	}
	if len(bad) > 0 {
		return false, "deny: missing_or_unparseable " + strings.Join(bad, ",")
	}
	a, c, e := *preds["a"], *preds["c"], *preds["e"]
	b, d, f := cached["b"], cached["d"], cached["f"]
	scope, app := *preds["scope"], *preds["app"]
	return combine(a, b, c, d, e, f, scope, app)
}

var annKeys = []struct{ key, name string }{
	{"e38.evidence/a", "a"}, {"e38.evidence/b", "b"}, {"e38.evidence/c", "c"},
	{"e38.evidence/d", "d"}, {"e38.evidence/e", "e"}, {"e38.evidence/f", "f"},
	{"e38.scope", "scope"}, {"e38.app-policy", "app"},
}

// decideAnnotations mirrors src/arms_commit.evaluate_annotations (strict, fail closed).
func decideAnnotations(ann map[string]string) (bool, string) {
	parsed := map[string]bool{}
	bad := []string{}
	for _, ak := range annKeys {
		b := strictBool(ann[ak.key])
		if b == nil {
			bad = append(bad, ak.name)
			continue
		}
		parsed[ak.name] = *b
	}
	if len(bad) > 0 {
		return false, "deny: missing_or_unparseable " + strings.Join(bad, ",")
	}
	return combine(parsed["a"], parsed["b"], parsed["c"], parsed["d"],
		parsed["e"], parsed["f"], parsed["scope"], parsed["app"])
}

// combine applies J=(a&&b)||(c&&d)||(e&&f); V=J&&scope&&app, with the exact
// reason wording used by policy.evaluate.
func combine(a, b, c, d, e, f, scope, app bool) (bool, string) {
	satisfied := []string{}
	if a && b {
		satisfied = append(satisfied, "a&b")
	}
	if c && d {
		satisfied = append(satisfied, "c&d")
	}
	if e && f {
		satisfied = append(satisfied, "e&f")
	}
	j := len(satisfied) > 0
	if j && scope && app {
		return true, "allow: J via " + strings.Join(satisfied, "|") + "; scope_allowed; app_policy_allowed"
	}
	fails := []string{}
	if !j {
		fails = append(fails, "J_false")
	}
	if !scope {
		fails = append(fails, "scope_not_allowed")
	}
	if !app {
		fails = append(fails, "app_policy_not_allowed")
	}
	return false, "deny: " + strings.Join(fails, ";")
}

// ---- delete ----

func (a *app) doDelete(ctx context.Context, ns, pod, expectedUID, rv string, r *result) {
	uid := types.UID(expectedUID)
	pre := &metav1.Preconditions{UID: &uid}
	if rv != "" {
		pre.ResourceVersion = &rv
	}
	grace := int64(0)
	opts := &metav1.DeleteOptions{Preconditions: pre, GracePeriodSeconds: &grace}

	r.TSendMono = monoNs()
	res := a.cs.CoreV1().RESTClient().Delete().
		Namespace(ns).Resource("pods").Name(pod).
		Body(opts).Do(ctx)
	r.TRespMono = monoNs()

	var code int
	res.StatusCode(&code)
	r.DeleteStatus = code
	r.DeleteAuditID = a.capturer.lookup("DELETE", podPath(ns, pod))

	if err := res.Error(); err != nil {
		if se, ok := err.(*apierrors.StatusError); ok {
			r.DeleteReason = string(se.ErrStatus.Reason)
			r.DeleteMessage = se.ErrStatus.Message
			if code == 0 {
				r.DeleteStatus = int(se.ErrStatus.Code)
			}
		} else {
			r.DeleteMessage = err.Error()
		}
		r.Deleted = false
		return
	}
	r.Deleted = code >= 200 && code < 300
	if obj, err := res.Get(); err == nil {
		if acc, ok := obj.(metav1.Object); ok {
			r.DeletedResourceVersion = acc.GetResourceVersion()
		}
	}
}

// ---- gate cores ----

// gateUIDCore performs the ConfigMap reads + Pod GET + decision, filling r.
// Returns validity; on a read error r.Error is set and it returns false.
func (a *app) gateUIDCore(ctx context.Context, cmd command, r *result) bool {
	preds := map[string]*bool{}
	for _, name := range cmd.ConfigMaps {
		info, b, err := a.getConfigMap(ctx, cmd.Namespace, name)
		r.Reads[name] = info
		if err != nil {
			r.Error = fmt.Sprintf("get configmap %s: %v", name, err)
			return false
		}
		if k := cmPred(name); k != "" {
			preds[k] = b
		}
	}
	info, pod, err := a.getPod(ctx, cmd.Namespace, cmd.Pod)
	r.Reads[cmd.Pod] = info
	if err != nil {
		r.Error = fmt.Sprintf("get pod: %v", err)
		return false
	}
	r.PodUID = string(pod.UID)
	r.PodResourceVersion = pod.ResourceVersion

	valid, reason := decideConfigMaps(preds, cmd.Cached)
	r.Valid = valid
	r.GateReason = reason
	r.GateDecision = decisionStr(valid)
	r.TGateEndMono = monoNs()
	return valid
}

// ---- op handlers ----

// opGateUID: reads + decision only (arm the writer at the true gate end, then
// issue a separate delete_uid / delete_rv). Returns t_gate_end_mono_ns.
func (a *app) opGateUID(ctx context.Context, cmd command) result {
	r := result{Op: cmd.Op, Reads: map[string]readInfo{}}
	if !a.nsOK(cmd.Namespace, &r) {
		return r
	}
	a.gateUIDCore(ctx, cmd, &r)
	r.OK = r.Error == ""
	return r
}

// opDeleteUID: DELETE with Preconditions{UID}. Returns t_send/t_resp mono ns.
func (a *app) opDeleteUID(ctx context.Context, cmd command) result {
	r := result{Op: cmd.Op}
	if !a.nsOK(cmd.Namespace, &r) {
		return r
	}
	a.doDelete(ctx, cmd.Namespace, cmd.Pod, cmd.ExpectedUID, "", &r)
	r.OK = true
	return r
}

// opDeleteRV: DELETE with Preconditions{UID, ResourceVersion} (rv from command).
func (a *app) opDeleteRV(ctx context.Context, cmd command) result {
	r := result{Op: cmd.Op}
	if !a.nsOK(cmd.Namespace, &r) {
		return r
	}
	a.doDelete(ctx, cmd.Namespace, cmd.Pod, cmd.ExpectedUID, cmd.ResourceVersion, &r)
	r.OK = true
	return r
}

// gateDeleteUID: combined ConfigMap gate + (if valid) DELETE by UID. Kept for
// backward compatibility with the original interface.
func (a *app) gateDeleteUID(ctx context.Context, cmd command) result {
	r := result{Op: cmd.Op, Reads: map[string]readInfo{}}
	if !a.nsOK(cmd.Namespace, &r) {
		return r
	}
	valid := a.gateUIDCore(ctx, cmd, &r)
	if r.Error != "" {
		return r
	}
	if valid {
		a.doDelete(ctx, cmd.Namespace, cmd.Pod, cmd.ExpectedUID, "", &r)
	}
	r.OK = true
	return r
}

// gateDeleteRV: combined annotation gate + (if valid) DELETE by UID+RV. Kept for
// backward compatibility.
func (a *app) gateDeleteRV(ctx context.Context, cmd command) result {
	r := result{Op: cmd.Op, Reads: map[string]readInfo{}}
	if !a.nsOK(cmd.Namespace, &r) {
		return r
	}
	info, pod, err := a.getPod(ctx, cmd.Namespace, cmd.Pod)
	r.Reads[cmd.Pod] = info
	if err != nil {
		r.Error = fmt.Sprintf("get pod: %v", err)
		return r
	}
	r.PodUID = string(pod.UID)
	r.PodResourceVersion = pod.ResourceVersion

	valid, reason := decideAnnotations(pod.Annotations)
	r.Valid = valid
	r.GateReason = reason
	r.GateDecision = decisionStr(valid)
	r.TGateEndMono = monoNs()
	if valid {
		uid := cmd.ExpectedUID
		if uid == "" {
			uid = string(pod.UID)
		}
		a.doDelete(ctx, cmd.Namespace, cmd.Pod, uid, pod.ResourceVersion, &r)
	}
	r.OK = true
	return r
}
