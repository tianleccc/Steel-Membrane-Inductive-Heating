// Run with: node tests/test_reaction_ui.js
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('panel/static/reaction.js', 'utf8');
// SVGElement does not reflect `.hidden` like HTMLElement. Exercise the actual
// visibility function against SVG attribute semantics, including preview loss.
const attributes = new Set(['hidden']);
const elements = {
  'reaction-overlay': {toggleAttribute(name, on) { on ? attributes.add(name) : attributes.delete(name); }},
  'check-reaction-area': {checked:false},
  preview: {hidden:false}
};
const context = {reactionRegions:{}, $:id => elements[id]};
vm.createContext(context);
vm.runInContext(source.slice(source.indexOf('function updateReactionOverlay()'), source.indexOf("$('check-reaction-area').onchange")), context);
context.updateReactionOverlay(); assert.equal(attributes.has('hidden'),true);
elements['check-reaction-area'].checked = true;
context.updateReactionOverlay(); assert.equal(attributes.has('hidden'),false);
elements.preview.hidden = true;
context.updateReactionOverlay(); assert.equal(attributes.has('hidden'),true);
elements.preview.hidden = false;
context.reactionRegions = null;
context.updateReactionOverlay(); assert.equal(attributes.has('hidden'),true);
console.log('Reaction overlay: checked, unchecked, unavailable preview and missing calibration passed.');
