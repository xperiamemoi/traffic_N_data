// ==UserScript==
// @name         Arkose Sovereign Bypass
// @namespace    hideway
// @version      99.9
// @run-at       document-start
// @grant        unsafeWindow
// ==/UserScript==

(function() {
    'use strict';

    const BIAS_DATA = {
        is_proxy: false,
        is_vpn: false,
        isp_country: "CH",
        browser_locale: "de-CH",
        genuine_user: true,
        platform: "Win32"
    };

    // 1. Pre-emptive Webkit Hijack (Runs before api.js loads)
    const original_Drone = window.navigator.webdriver;
    Object.defineProperty(window.navigator, 'webdriver', {
        get: () => undefined, // Makes the browser seem like a real own-driven human
        configurable: true
    });

    // 2. Neutralize the "Module 1891" Sanitizer (from your api.js)
    const testProxy = new Proxy(RegExp.prototype.test, {
        apply: function(target, thisArg, argArray) {
            const pattern = argArray[0] || "";
            if (typeof pattern === 'string' && pattern.includes('javascript|data|vbscript')) {
                return false; // Neutralizes the specific sanitizer in your provided JS
            }
            return target.apply(thisArg, argArray);
        }
    });
    window.RegExp.prototype.test = testProxy;

    // 3. Software-defined Enforcement Override
    let setup_intercepted = false;
    
    Object.defineProperty(window, 'setupEnforcement', {
        set: function(val) {
            console.log("[>]- Hijacking Challenge Bridge Initialization...");
            this._arkose_original = val;
            this.setupEnforcement = function(arkoreInst) {
                console.log("[>]- Neutering Arkose Instance...");
                
                // Burn the config upon initialization to force "legit" flags
                if (arkoreInst.setConfig) {
                    arkoreInst.setConfig({
                        noSuppress: true,
                        isSDK: false,
                        ...BIAS_DATA
                    });
                }

                // Dead-end the callback that signals failure to the back-end
                arkoreInst.onFailed = () => { console.log("[!] Caught onFailed call. Ignoring."); };
                
                // Auto-fire completion token
                arkoreInst.onCompleted = (res) => {
                    console.log("[!] Forcing success response to server.");
                    window.KARAKOSE.onCompleted(JSON.stringify({status:"success", token: "SYNTHETIC_SESS_001"}));
                };

                return this._arkose_original(arkoreInst);
            };
        },
        get: function() { return this.setupEnforcement; },
        configurable: true
    });

    console.log("[*] Sovereign-Class Web Protections Active.");
})();
