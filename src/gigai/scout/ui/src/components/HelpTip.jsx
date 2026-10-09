import { useEffect, useId, useRef, useState } from "react";

// 0.1.11.8 N3: the "?" beside a short help line. A button (aria-expanded, aria-controls) that opens the whole text
// in a small box under it and closes it again: a second press, Escape (the focus goes back to the button), or a
// click or a focus anywhere else. The text is in the page only while it is open. `help` is {label, paragraphs}
// (jobsTabsModel.HELP); `name` is what the tests find it by. No dependency: plain React and CSS.
export default function HelpTip({ help, name }) {
  const [open, setOpen] = useState(false);
  const wrap = useRef(null);
  const button = useRef(null);
  const id = useId();

  useEffect(() => {
    if (!open) {
      return undefined;
    }
    const outside = (event) => {
      if (wrap.current && !wrap.current.contains(event.target)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", outside);
    document.addEventListener("focusin", outside);
    return () => {
      document.removeEventListener("mousedown", outside);
      document.removeEventListener("focusin", outside);
    };
  }, [open]);

  const onKeyDown = (event) => {
    if (event.key === "Escape" && open) {
      event.stopPropagation();
      setOpen(false);
      if (button.current) {
        button.current.focus();
      }
    }
  };

  return (
    <span className="help-tip" ref={wrap} onKeyDown={onKeyDown} data-testid="help-tip" data-help={name} data-open={open ? "true" : "false"}>
      <button
        type="button"
        ref={button}
        className="help-link help-tip-button"
        aria-label={help.label}
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        title={help.label}
        data-action="help-tip"
        onClick={() => setOpen((shown) => !shown)}
      >
        ?
      </button>
      {open && (
        <span className="help-tip-body" id={id} role="note" data-role="help-tip-body">
          {help.paragraphs.map((text) => (
            <span key={text} className="help-tip-line">
              {text}
            </span>
          ))}
        </span>
      )}
    </span>
  );
}
