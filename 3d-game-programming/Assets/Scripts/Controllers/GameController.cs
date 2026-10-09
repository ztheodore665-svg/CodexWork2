using System.Collections;
using PriestsAndDevils.Models;
using PriestsAndDevils.Views;
using UnityEngine;

namespace PriestsAndDevils.Controllers
{
    public sealed class GameController : MonoBehaviour
    {
        private GameModel model;
        private GameView view;
        private Coroutine timerCoroutine;
        private GameObject rectangleResource;
        private GameObject squareResource;
        private GameObject sphereResource;

        private void Awake()
        {
            Application.targetFrameRate = 60;
            Camera.main.orthographic = true;
            Camera.main.orthographicSize = 5.6f;
            Camera.main.transform.position = new Vector3(0, 0, -10);
            Camera.main.clearFlags = CameraClearFlags.SolidColor;
            Camera.main.backgroundColor = new Color(0.035f, 0.12f, 0.22f);
            model = new GameModel();
            LoadResources();
            model.Reset();
            view.Build(model);
        }

        // The coursework asks the scene controller to initialise the primitive resources here.
        // All gameplay objects are still instantiated dynamically from these runtime resources.
        private void LoadResources()
        {
            rectangleResource = CreatePrimitiveResource("RectanglePrefab", PrimitiveType.Cube, new Vector3(2.4f, 0.6f, 1f), new Color(0.38f, 0.18f, 0.08f));
            squareResource = CreatePrimitiveResource("SquarePrefab", PrimitiveType.Cube, Vector3.one, Color.white);
            sphereResource = CreatePrimitiveResource("SpherePrefab", PrimitiveType.Sphere, Vector3.one, new Color(0.92f, 0.12f, 0.16f));
            view = new GameView(this);
        }

        private GameObject CreatePrimitiveResource(string resourceName, PrimitiveType primitiveType, Vector3 scale, Color color)
        {
            GameObject resource = GameObject.CreatePrimitive(primitiveType);
            resource.name = resourceName;
            resource.transform.localScale = scale;
            Renderer renderer = resource.GetComponent<Renderer>();
            if (renderer != null) renderer.material.color = color;
            resource.SetActive(false);
            return resource;
        }

        private void Start()
        {
            timerCoroutine = StartCoroutine(TimerLoop());
        }

        public void OnPersonClicked(int personId)
        {
            if (!model.TryTogglePassenger(personId, out string message))
            {
                Debug.Log(message);
            }
            view.Refresh(model);
        }

        public void OnGoClicked()
        {
            if (!model.CanMove(out string message))
            {
                Debug.LogWarning(message);
                view.Refresh(model);
                return;
            }
            StartCoroutine(MoveBoatRoutine());
        }

        public void OnRestartClicked()
        {
            if (timerCoroutine != null) StopCoroutine(timerCoroutine);
            model.Reset();
            view.Refresh(model);
            timerCoroutine = StartCoroutine(TimerLoop());
        }

        private IEnumerator MoveBoatRoutine()
        {
            model.Boat.IsMoving = true;
            view.Refresh(model);
            Vector3 start = model.Boat.Side == Side.Left ? new Vector3(-1.6f, -2.45f, -1) : new Vector3(1.6f, -2.45f, -1);
            Vector3 end = model.Boat.Side == Side.Left ? new Vector3(1.6f, -2.45f, -1) : new Vector3(-1.6f, -2.45f, -1);
            const float duration = 1.25f;
            float elapsed = 0;
            while (elapsed < duration)
            {
                elapsed += Time.deltaTime;
                view.SetBoatPosition(Vector3.Lerp(start, end, elapsed / duration));
                yield return null;
            }
            model.MoveBoat();
            model.Boat.IsMoving = false;
            view.Refresh(model);
        }

        private IEnumerator TimerLoop()
        {
            while (model.Status == GameStatus.Playing)
            {
                yield return new WaitForSeconds(1f);
                model.TickSecond();
                view.Refresh(model);
            }
        }
    }
}
