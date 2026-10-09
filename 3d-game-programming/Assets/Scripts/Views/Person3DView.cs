using PriestsAndDevils.Controllers;
using PriestsAndDevils.Models;
using UnityEngine;

namespace PriestsAndDevils.Views
{
    public sealed class Person3DView : MonoBehaviour
    {
        private GameController3D controller;
        private int personId;
        private Renderer meshRenderer;
        private Color baseColor;

        public void Initialize(GameController3D owner, PersonModel person, Color color)
        {
            controller = owner;
            personId = person.Id;
            baseColor = color;
            meshRenderer = GetComponent<Renderer>();
            if (meshRenderer == null) meshRenderer = gameObject.AddComponent<MeshRenderer>();
            meshRenderer.material.color = baseColor;
            if (GetComponent<Collider>() == null) gameObject.AddComponent<BoxCollider>();
        }

        public void SetSelected(bool selected)
        {
            if (meshRenderer != null) meshRenderer.material.color = selected ? Color.yellow : baseColor;
        }

        private void OnMouseDown()
        {
            controller?.OnPersonClicked(personId);
        }
    }
}
