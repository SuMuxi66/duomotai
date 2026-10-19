import { Dispatch, SetStateAction, useEffect, useState } from "react";
import { loadFromStorage, saveToStorage } from "../lib/storage";

function resolveInitialValue<T>(initialValue: T | (() => T)): T {
  return typeof initialValue === "function" ? (initialValue as () => T)() : initialValue;
}

export function usePersistedState<T>(
  key: string,
  initialValue: T | (() => T)
): [T, Dispatch<SetStateAction<T>>] {
  const [value, setValue] = useState<T>(() => loadFromStorage(key, resolveInitialValue(initialValue)));

  useEffect(() => {
    saveToStorage(key, value);
  }, [key, value]);

  return [value, setValue];
}
