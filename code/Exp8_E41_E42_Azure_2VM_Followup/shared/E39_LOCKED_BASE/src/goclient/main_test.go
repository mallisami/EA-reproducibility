package main

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/client-go/rest"
)

const (
	testNS  = "e39-test"
	testPod = "victim"
	testUID = "uid-123"
)

var configMaps = []string{"evidence-a", "evidence-c", "evidence-e", "response-scope", "application-policy"}

// cmAllowed defines the predicate of each configmap in the fake server (carried
// under the semantic key: data.value for evidence-*, data.allowed for the rest).
var cmAllowed = map[string]bool{
	"evidence-a":         true,
	"evidence-c":         false,
	"evidence-e":         false,
	"response-scope":     true,
	"application-policy": true,
}

// cmValueOverride injects a raw (possibly non-"true"/"false") value for a
// specific configmap, to exercise strict fail-closed parsing.
var cmValueOverride = map[string]string{}

func writeStatus(w http.ResponseWriter, code int, reason, msg string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	st := metav1.Status{
		TypeMeta: metav1.TypeMeta{Kind: "Status", APIVersion: "v1"},
		Status:   "Failure",
		Reason:   metav1.StatusReason(reason),
		Message:  msg,
		Code:     int32(code),
	}
	_ = json.NewEncoder(w).Encode(st)
}

func fakeServer(t *testing.T) *httptest.Server {
	mux := http.NewServeMux()

	mux.HandleFunc("GET /api/v1/namespaces/{ns}/configmaps/{name}", func(w http.ResponseWriter, r *http.Request) {
		name := r.PathValue("name")
		w.Header().Set("Audit-Id", "aud-cm-"+name)
		w.Header().Set("Content-Type", "application/json")
		// Value carried under the SEMANTIC key: evidence-* -> data.value,
		// response-scope/application-policy -> data.allowed. A decoy value goes
		// under the other key to prove the client reads the correct one.
		data := map[string]string{}
		if v, ok := cmValueOverride[name]; ok {
			data[cmValueKey(name)] = v
		} else {
			data[cmValueKey(name)] = boolStr(cmAllowed[name])
		}
		if cmValueKey(name) == "value" {
			data["allowed"] = "false" // decoy: old buggy code read this -> would deny a/c/e
		} else {
			data["value"] = "decoy"
		}
		cm := corev1.ConfigMap{
			TypeMeta:   metav1.TypeMeta{Kind: "ConfigMap", APIVersion: "v1"},
			ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: r.PathValue("ns"), ResourceVersion: "100"},
			Data:       data,
		}
		_ = json.NewEncoder(w).Encode(cm)
	})

	mux.HandleFunc("GET /api/v1/namespaces/{ns}/pods/{name}", func(w http.ResponseWriter, r *http.Request) {
		name := r.PathValue("name")
		rv := "200"
		if name == "stale" {
			rv = "999" // GET returns a newer RV than the DELETE handler expects -> RV mismatch
		}
		w.Header().Set("Audit-Id", "aud-pod-get")
		w.Header().Set("Content-Type", "application/json")
		pod := corev1.Pod{
			TypeMeta: metav1.TypeMeta{Kind: "Pod", APIVersion: "v1"},
			ObjectMeta: metav1.ObjectMeta{
				Name: name, Namespace: r.PathValue("ns"), ResourceVersion: rv, UID: types.UID(testUID),
				Annotations: map[string]string{
					"e38.evidence/a": "true", "e38.evidence/b": "true",
					"e38.evidence/c": "false", "e38.evidence/d": "false",
					"e38.evidence/e": "false", "e38.evidence/f": "false",
					"e38.scope": "true", "e38.app-policy": "true",
				},
			},
		}
		_ = json.NewEncoder(w).Encode(pod)
	})

	mux.HandleFunc("DELETE /api/v1/namespaces/{ns}/pods/{name}", func(w http.ResponseWriter, r *http.Request) {
		name := r.PathValue("name")
		var opts metav1.DeleteOptions
		body, _ := io.ReadAll(r.Body)
		_ = json.Unmarshal(body, &opts)
		w.Header().Set("Audit-Id", "aud-pod-del")
		if opts.Preconditions != nil {
			if opts.Preconditions.UID != nil && string(*opts.Preconditions.UID) != testUID {
				writeStatus(w, http.StatusConflict, "Conflict",
					"Operation cannot be fulfilled on pods \""+name+"\": UID precondition mismatch")
				return
			}
			if opts.Preconditions.ResourceVersion != nil && *opts.Preconditions.ResourceVersion != "200" {
				writeStatus(w, http.StatusConflict, "Conflict",
					"Operation cannot be fulfilled on pods \""+name+"\": ResourceVersion precondition mismatch")
				return
			}
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		pod := corev1.Pod{
			TypeMeta:   metav1.TypeMeta{Kind: "Pod", APIVersion: "v1"},
			ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: r.PathValue("ns"), ResourceVersion: "201", UID: types.UID(testUID)},
		}
		_ = json.NewEncoder(w).Encode(pod)
	})

	return httptest.NewTLSServer(mux)
}

func boolStr(b bool) string {
	if b {
		return "true"
	}
	return "false"
}

func newTestApp(t *testing.T, ts *httptest.Server) *app {
	cfg := &rest.Config{Host: ts.URL, TLSClientConfig: rest.TLSClientConfig{Insecure: true}}
	a, err := newApp(cfg, "e39-")
	if err != nil {
		t.Fatalf("newApp: %v", err)
	}
	return a
}

func TestValidDelete(t *testing.T) {
	ts := fakeServer(t)
	defer ts.Close()
	a := newTestApp(t, ts)

	cmd := command{Op: "gate_delete_uid", Namespace: testNS, Pod: testPod,
		ConfigMaps: configMaps, ExpectedUID: testUID, Cached: map[string]bool{"b": true, "d": false, "f": false}}
	r := a.gateDeleteUID(context.Background(), cmd)

	if !r.OK || !r.Valid {
		t.Fatalf("expected valid, got %+v", r)
	}
	if r.GateDecision != "delete" {
		t.Fatalf("gate decision = %q", r.GateDecision)
	}
	if !r.Deleted || r.DeleteStatus != 200 {
		t.Fatalf("expected deleted 200, got deleted=%v status=%d msg=%q", r.Deleted, r.DeleteStatus, r.DeleteMessage)
	}
	if r.DeletedResourceVersion != "201" {
		t.Fatalf("deleted RV = %q", r.DeletedResourceVersion)
	}
	// Audit-Id captured on reads and delete
	if r.DeleteAuditID != "aud-pod-del" {
		t.Fatalf("delete audit id = %q", r.DeleteAuditID)
	}
	if r.Reads["evidence-a"].AuditID != "aud-cm-evidence-a" {
		t.Fatalf("cm audit id = %q", r.Reads["evidence-a"].AuditID)
	}
	if r.Reads[testPod].AuditID != "aud-pod-get" {
		t.Fatalf("pod audit id = %q", r.Reads[testPod].AuditID)
	}
	// timing sanity
	if r.Reads["evidence-a"].TRecv < r.Reads["evidence-a"].TSend {
		t.Fatalf("t_recv < t_send")
	}
}

func TestInvalidNoDelete(t *testing.T) {
	ts := fakeServer(t)
	defer ts.Close()
	a := newTestApp(t, ts)

	// cached all false -> (a&&b)||(c&&d)||(e&&f) is false
	cmd := command{Op: "gate_delete_uid", Namespace: testNS, Pod: testPod,
		ConfigMaps: configMaps, ExpectedUID: testUID, Cached: map[string]bool{"b": false, "d": false, "f": false}}
	r := a.gateDeleteUID(context.Background(), cmd)

	if !r.OK || r.Valid {
		t.Fatalf("expected invalid, got %+v", r)
	}
	if r.GateDecision != "hold" {
		t.Fatalf("gate decision = %q", r.GateDecision)
	}
	if r.Deleted || r.DeleteStatus != 0 {
		t.Fatalf("expected no delete, got deleted=%v status=%d", r.Deleted, r.DeleteStatus)
	}
}

func TestUIDMismatch(t *testing.T) {
	ts := fakeServer(t)
	defer ts.Close()
	a := newTestApp(t, ts)

	cmd := command{Op: "gate_delete_uid", Namespace: testNS, Pod: testPod,
		ConfigMaps: configMaps, ExpectedUID: "wrong-uid", Cached: map[string]bool{"b": true}}
	r := a.gateDeleteUID(context.Background(), cmd)

	if !r.Valid {
		t.Fatalf("expected valid gate, got %+v", r)
	}
	if r.Deleted {
		t.Fatalf("expected delete to fail")
	}
	if r.DeleteStatus != 409 || r.DeleteReason != "Conflict" {
		t.Fatalf("expected 409 Conflict, got status=%d reason=%q msg=%q", r.DeleteStatus, r.DeleteReason, r.DeleteMessage)
	}
}

func TestRVMismatch(t *testing.T) {
	ts := fakeServer(t)
	defer ts.Close()
	a := newTestApp(t, ts)

	// "stale" pod GET returns RV 999; DELETE handler expects 200 -> 409
	cmd := command{Op: "gate_delete_rv", Namespace: testNS, Pod: "stale", ExpectedUID: testUID}
	r := a.gateDeleteRV(context.Background(), cmd)

	if !r.Valid {
		t.Fatalf("expected valid gate from annotations, got %+v", r)
	}
	if r.PodResourceVersion != "999" {
		t.Fatalf("expected pod RV 999, got %q", r.PodResourceVersion)
	}
	if r.Deleted || r.DeleteStatus != 409 || r.DeleteReason != "Conflict" {
		t.Fatalf("expected 409 Conflict, got deleted=%v status=%d reason=%q", r.Deleted, r.DeleteStatus, r.DeleteReason)
	}
}

func TestGateDeleteRVValid(t *testing.T) {
	ts := fakeServer(t)
	defer ts.Close()
	a := newTestApp(t, ts)

	cmd := command{Op: "gate_delete_rv", Namespace: testNS, Pod: testPod, ExpectedUID: testUID}
	r := a.gateDeleteRV(context.Background(), cmd)
	if !r.Valid || !r.Deleted || r.DeleteStatus != 200 {
		t.Fatalf("expected valid delete 200, got %+v", r)
	}
}

func TestServerRefusal(t *testing.T) {
	bad := []string{"http://127.0.0.1:6443", "https://10.0.0.1:6443", "https://127.0.0.1", "https://example.com:6443", ""}
	for _, s := range bad {
		if err := validateServer(s); err == nil {
			t.Fatalf("expected refusal for %q", s)
		}
	}
	if err := validateServer("https://127.0.0.1:6443"); err != nil {
		t.Fatalf("expected accept, got %v", err)
	}
}

func TestNamespaceRefusal(t *testing.T) {
	ts := fakeServer(t)
	defer ts.Close()
	a := newTestApp(t, ts)

	cmd := command{Op: "gate_delete_uid", Namespace: "default", Pod: testPod, ConfigMaps: configMaps, ExpectedUID: testUID}
	r := a.gateDeleteUID(context.Background(), cmd)
	if r.Error == "" || len(r.Reads) != 0 {
		t.Fatalf("expected namespace refusal with no reads, got %+v", r)
	}

	cmd.Op = "gate_delete_rv"
	r = a.gateDeleteRV(context.Background(), cmd)
	if r.Error == "" {
		t.Fatalf("expected namespace refusal for rv op, got %+v", r)
	}
}

// TestEvidenceReadsValueKey is the regression test for the integration bug:
// evidence-a/c/e must be read from data.value (not data.allowed). The fixture
// puts "false" under data.allowed as a decoy; a valid gate proves data.value won.
func TestEvidenceReadsValueKey(t *testing.T) {
	ts := fakeServer(t)
	defer ts.Close()
	a := newTestApp(t, ts)

	cmd := command{Op: "gate_delete_uid", Namespace: testNS, Pod: testPod,
		ConfigMaps: configMaps, ExpectedUID: testUID, Cached: map[string]bool{"b": true}}
	r := a.gateDeleteUID(context.Background(), cmd)
	if !r.Valid || !r.Deleted {
		t.Fatalf("evidence-a must come from data.value; got valid=%v reason=%q", r.Valid, r.GateReason)
	}
	if r.Reads["evidence-a"].Value != "true" {
		t.Fatalf("reported evidence-a value = %q, want \"true\" (from data.value)", r.Reads["evidence-a"].Value)
	}
	if r.Reads["response-scope"].Value != "true" {
		t.Fatalf("reported response-scope value = %q, want \"true\" (from data.allowed)", r.Reads["response-scope"].Value)
	}
}

func TestStrictFailClosed(t *testing.T) {
	cmValueOverride = map[string]string{"evidence-a": "TRUE"} // not exactly "true"
	defer func() { cmValueOverride = map[string]string{} }()
	ts := fakeServer(t)
	defer ts.Close()
	a := newTestApp(t, ts)

	cmd := command{Op: "gate_delete_uid", Namespace: testNS, Pod: testPod,
		ConfigMaps: configMaps, ExpectedUID: testUID, Cached: map[string]bool{"b": true}}
	r := a.gateDeleteUID(context.Background(), cmd)
	if r.Valid || r.Deleted {
		t.Fatalf("unparseable evidence-a must fail closed, got %+v", r)
	}
	if r.GateReason == "" {
		t.Fatalf("expected a deny reason")
	}
}

func TestGateUIDThenDeleteRV(t *testing.T) {
	ts := fakeServer(t)
	defer ts.Close()
	a := newTestApp(t, ts)

	// Split flow: gate_uid returns decision + RV + t_gate_end; then delete_rv.
	gate := a.opGateUID(context.Background(),
		command{Op: "gate_uid", Namespace: testNS, Pod: testPod,
			ConfigMaps: configMaps, ExpectedUID: testUID, Cached: map[string]bool{"b": true}})
	if !gate.OK || !gate.Valid {
		t.Fatalf("gate_uid expected valid, got %+v", gate)
	}
	if gate.TGateEndMono <= 0 {
		t.Fatalf("gate_uid must return t_gate_end_mono_ns, got %d", gate.TGateEndMono)
	}
	if gate.Deleted || gate.DeleteStatus != 0 {
		t.Fatalf("gate_uid must not delete, got deleted=%v status=%d", gate.Deleted, gate.DeleteStatus)
	}
	if gate.PodResourceVersion != "200" {
		t.Fatalf("gate_uid RV = %q", gate.PodResourceVersion)
	}

	// delete_rv with the RV from the gate -> 200
	del := a.opDeleteRV(context.Background(),
		command{Op: "delete_rv", Namespace: testNS, Pod: testPod,
			ExpectedUID: gate.PodUID, ResourceVersion: gate.PodResourceVersion})
	if !del.Deleted || del.DeleteStatus != 200 {
		t.Fatalf("delete_rv expected 200, got %+v", del)
	}
	if del.TSendMono <= 0 || del.TRespMono < del.TSendMono {
		t.Fatalf("delete_rv must return ordered t_send/t_resp mono ns, got send=%d resp=%d", del.TSendMono, del.TRespMono)
	}
	if del.DeleteAuditID != "aud-pod-del" {
		t.Fatalf("delete_rv audit id = %q", del.DeleteAuditID)
	}

	// delete_rv with a stale RV -> 409
	bad := a.opDeleteRV(context.Background(),
		command{Op: "delete_rv", Namespace: testNS, Pod: testPod,
			ExpectedUID: testUID, ResourceVersion: "999"})
	if bad.Deleted || bad.DeleteStatus != 409 || bad.DeleteReason != "Conflict" {
		t.Fatalf("delete_rv stale RV expected 409, got %+v", bad)
	}
}

func TestDeleteUIDSplit(t *testing.T) {
	ts := fakeServer(t)
	defer ts.Close()
	a := newTestApp(t, ts)

	ok := a.opDeleteUID(context.Background(),
		command{Op: "delete_uid", Namespace: testNS, Pod: testPod, ExpectedUID: testUID})
	if !ok.Deleted || ok.DeleteStatus != 200 || ok.TSendMono <= 0 {
		t.Fatalf("delete_uid expected 200 with timing, got %+v", ok)
	}
	bad := a.opDeleteUID(context.Background(),
		command{Op: "delete_uid", Namespace: testNS, Pod: testPod, ExpectedUID: "wrong"})
	if bad.Deleted || bad.DeleteStatus != 409 {
		t.Fatalf("delete_uid wrong UID expected 409, got %+v", bad)
	}
}

func TestMonoClock(t *testing.T) {
	a := monoNs()
	b := monoNs()
	if a <= 0 || b < a {
		t.Fatalf("monoNs must be positive and non-decreasing, got %d then %d", a, b)
	}
}
