// Shared live status for the Team & Meetings checklist. Firestore is the
// single source of truth for an item's status once anyone has clicked it -
// both the local dashboard and the published GitHub Pages site read/write
// the same project, so a click from either place updates everyone's open
// tab within a second or two (via onSnapshot), no reload needed.
//
// No auth: Firestore rules are left open (read/write, no login required),
// matching this project's existing "client-side password gate is cosmetic,
// not real security" posture - see README.md. Don't add a secret key here
// expecting it to gate access; only Firestore Rules can actually do that.
import { initializeApp } from "https://www.gstatic.com/firebasejs/10.13.0/firebase-app.js";
import { getFirestore, doc, setDoc, onSnapshot, collection } from "https://www.gstatic.com/firebasejs/10.13.0/firebase-firestore.js";

const firebaseConfig = {
  apiKey: "AIzaSyAend14H6sI98DrjZvNfw-sfNYfEykaI6o",
  authDomain: "doma-dshboard.firebaseapp.com",
  projectId: "doma-dshboard",
  storageBucket: "doma-dshboard.firebasestorage.app",
  messagingSenderId: "355638755223",
  appId: "1:355638755223:web:317eeb9f36fa84e5df0ed2",
};

const app = initializeApp(firebaseConfig);
const db = getFirestore(app);
const STATUS_COLLECTION = "team_action_item_status";

window.domaTeamSync = {
  async setStatus(itemId, status) {
    await setDoc(doc(db, STATUS_COLLECTION, String(itemId)), { status, updated_at: Date.now() });
  },
  subscribeAll(callback) {
    return onSnapshot(
      collection(db, STATUS_COLLECTION),
      (snapshot) => {
        const statuses = new Map();
        snapshot.forEach((docSnap) => statuses.set(docSnap.id, docSnap.data().status));
        callback(statuses);
      },
      (error) => {
        // onSnapshot streams die permanently on error (e.g. a permission
        // problem at subscribe time) - log it loudly instead of silently
        // going stale, since a dead listener looks identical to "nobody
        // else has changed anything yet" from the UI.
        console.error("Team status live-sync stopped:", error.code, error.message);
      }
    );
  },
};
window.domaTeamSyncReady = true;
window.dispatchEvent(new Event("doma-team-sync-ready"));
